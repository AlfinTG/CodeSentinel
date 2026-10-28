# CodeSentinel

**Evidence-led regression diagnosis and repair verification for GitHub projects.**

> Status: documentation/planning baseline only. No application implementation is included yet. Do not call a feature shipped until code, tests, and a reproducible demo prove it.

## Product promise
For one connected GitHub repository, CodeSentinel associates a pinned commit with deterministic configured checks, bounded/redacted failure evidence, and a structured diagnosis from an NVIDIA open-source model served through Nebius Token Factory. In a later phase, a candidate patch may be tested in isolation. **Observed test results are authoritative; model output is a hypothesis.**

## MVP flow
GitHub event → signature/installation checks → idempotent durable run → isolated configured checks → bounded evidence → model diagnosis → dashboard. Candidate patch verification is Phase 2 and must not delay the secure core flow.

## Locked baseline
- React + TypeScript + Vite; Python 3.12+ + FastAPI + Pydantic v2.
- PostgreSQL + SQLAlchemy 2.x + Alembic; Redis + RQ for asynchronous jobs.
- GitHub App with least-privilege repository permissions.
- Separate trusted worker/supervisor and ephemeral untrusted execution container.
- Nebius Token Factory server-side adapter; choose eligible NVIDIA open model from the live account-visible catalog.
- Controlled single-tenant demo first. No auto-merge, branch writes, or deployment.
- API never executes repository commands; runner never receives application/provider/GitHub secrets or a Docker socket.

## Start here
1. Read AGENTS.md, DECISIONS.md, PRD.md, ARCHITECTURE.md, SECURITY.md, API_CONTRACTS.md, DATABASE.md, TESTING.md.
2. Follow IMPLEMENTATION_PLAN.md in order. Do not scaffold every feature at once.
3. Confirm GitHub App permissions, provider/model access, deployment budget, and open-source license before relying on them.
4. Keep statuses honest: proposed, implemented, tested, and verified are different states.

## Documentation index
Product: SOLUTION_OVERVIEW.md, PRD.md. Architecture: ARCHITECTURE.md, DECISIONS.md, DATABASE.md, API_CONTRACTS.md. Security/quality: SECURITY.md, TESTING.md, MODEL_EVALUATION.md, CODE_STYLE.md. Delivery: IMPLEMENTATION_PLAN.md, SETUP_AND_CREATION_GUIDE.md, RELEASE_CHECKLIST.md, HACKATHON_COMPLIANCE.md. Team guides: AGENTS.md, CREATE_PROMPT.md, INSTALL_PROMPT.md, DESIGN_SYSTEM.md.

## Current status
Phases 0 through 6 are fully implemented and verified with automated test suites:
- **Phase 0:** Repository baseline, Apache 2.0 license, Docker Compose stack, CI pipeline, and security probes.
- **Phase 1:** PostgreSQL models, Alembic migration 0001, legal state transitions, and object-level authorization.
- **Phase 2:** Constant-time HMAC-SHA256 GitHub webhook verification, event filtering, and deduplicated durable run creation.
- **Phase 3:** Isolated execution runner, environment stripping, binary allowlisting, 256 KiB output bounding, and secret redaction.
- **Phase 4:** Nebius Token Factory adapter with eligible NVIDIA model, strict JSON schema validation, evidence-ID cross-referencing, and honest failure isolation.
- **Phase 5:** Interactive React + TypeScript + Vite developer dashboard, multi-panel run details, check/evidence inspector, AI diagnosis panel, run cancellation, and demo seed pipeline.
- **Phase 6 (P1):** Candidate patch proposal engine, safe unified diff application, isolated ephemeral sandbox verification, and attempt history (zero branch writes, PR creation, or auto-merges).
- **Phase 7:** Clean-environment production prototype deployment and live demonstration rehearsal.

## References
- GitHub webhook signatures: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- GitHub Apps: https://docs.github.com/en/apps/creating-github-apps
- Nebius Token Factory API: https://api.tokenfactory.nebius.com/docs
- Token Factory overview: https://dev.nebius.com/token-factory
- PostgreSQL: https://www.postgresql.org/docs/
- Playwright: https://playwright.dev/docs/intro
