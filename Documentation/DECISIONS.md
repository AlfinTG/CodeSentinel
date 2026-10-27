# Baseline Decisions and Open Questions

These are implementation defaults, not claims about an existing application. Do not reopen them without evidence; record rationale, impact and migration plan for any change.

| ID | Decision | Boundary |
|---|---|---|
| D-001 | Monorepo; docs at root, source in frontend/, backend/, runner/ as justified | Cohesive modules |
| D-002 | React + TypeScript + Vite | No SSR requirement in MVP |
| D-003 | Python 3.12+, FastAPI, Pydantic v2 | Typed API boundaries |
| D-004 | PostgreSQL, SQLAlchemy 2.x, Alembic | Migrations and DB constraints required |
| D-005 | Redis + RQ | Async jobs; durable run/outbox transaction required |
| D-006 | GitHub App and server-side session; least privilege | No long-lived personal token or branch-write permission in MVP |
| D-007 | Trusted worker/supervisor launches ephemeral constrained job container | Job has no Docker socket or app secrets |
| D-008 | Nebius Token Factory adapter | Choose eligible NVIDIA open model from live catalog; never guess model ID |
| D-009 | Provider URL/model/key from environment | Never hard-code credentials |
| D-010 | Strict structured diagnosis schema | Evidence IDs must belong to current run |
| D-011 | Test outcome and analysis outcome are independent | Provider failure never changes test result |
| D-012 | Candidate patch verification is Phase 2 | No PR creation, branch writes, merge or deploy in MVP |
| D-013 | Docker Compose local demo; API and worker separate | Reproducible local setup |
| D-014 | Controlled single-tenant demo first | No claim of hardened multi-tenant isolation |

## Verify before depending on these
GitHub App creation/permissions/callback; Nebius account and eligible model access/rates/budget; runner isolation on target host; deployment cost; license choice by owner; hackathon track fit and current rules.

## Reopening policy
Change a decision only when new evidence justifies it. Record context, evidence, chosen option, alternatives rejected, impact, migration/rollback, owner and date. Never turn an unverified assumption into a product claim.
