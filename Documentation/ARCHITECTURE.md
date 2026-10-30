# Architecture

## Goals
Trace source revision to test facts and diagnosis; durable/idempotent event processing; clear trust boundaries; isolated repository execution; understandable failure states.

## Reference flow
Browser → React/TypeScript → FastAPI → PostgreSQL. GitHub App sends webhook to FastAPI. API verifies raw-body signature and installation/repository, then transactionally persists delivery/run/outbox and acknowledges quickly. Redis/RQ transports work to a separate trusted worker/supervisor. Supervisor creates an ephemeral restricted job container for the pinned revision and approved checks. Bounded results are normalized/redacted and persisted. A server-side adapter calls the configured AI provider (Nebius Token Factory as reference provider, or NVIDIA NIM as alternative); validator checks model schema/evidence IDs. UI renders test outcome separately from analysis state.

## Components
- Frontend: presentation and explicit user actions; no provider credentials.
- API: auth, authorization, webhook verification, validation, persistence, queueing; never runs repository commands.
- GitHub adapter: event filtering, installation/repository identity, immutable SHA resolution.
- Coordinator: lifecycle transitions, retries/cancellation and attempt tracking.
- Trusted supervisor: fetches authorized source and creates/destroys execution containers; privileged infrastructure requiring a narrow interface.
- Untrusted job: non-root, resource/time/output bounded, minimal workspace, no secrets/socket/host mounts; network disabled by default.
- Evidence normalizer: size caps, redaction, truncation markers, factual test result normalization.
- Model adapter/validator: server-side provider call (Nebius Token Factory or NVIDIA NIM), timeouts, bounded retries, strict structured output and evidence-reference checks.
- PostgreSQL is source of truth; Redis/RQ is transport, not the only record of work.

## State model
Run lifecycle: received → queued → running → completed | failed | timed_out | cancelled | inconclusive. Analysis: not_requested → queued → running → succeeded | unavailable | invalid_response. Test outcome and analysis status are independent. State changes pass through transition functions; duplicate/late worker messages are idempotent.

## Reliability
Unique GitHub delivery ID and transactional run/outbox creation. Pin commit SHA and check-profile version before enqueue. Bound retries and record attempts. Worker disappearance/partial report maps to inconclusive/error, never pass. Readiness checks dependencies without exposing secrets. Structured logs include request/run IDs and safe codes. Provider outage cannot change test result.

## Security boundaries
API and supervisor are trusted; repository code/output are untrusted. Job container gets only checkout and approved check configuration; no app/database/GitHub/Nebius secrets, Docker socket, privileged mode or broad host mounts. Enforce CPU, memory, PIDs, disk, wall-clock and output limits. Public arbitrary-code execution remains disabled until isolation is reviewed and tested.

## Deployment baseline
Single-tenant controlled demo, Docker Compose for local dependencies, separate API/worker, PostgreSQL and Redis. Production hosting, secret manager, TLS, quotas, backup/restore and runner isolation require release verification. This target architecture is not proof of deployment or hardening.
