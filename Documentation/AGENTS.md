# Instructions for Coding Agents

## Before editing
1. Inspect tree, branch/status, package and lock files, environment examples, routes, migrations, tests, and CI.
2. Preserve existing work; never reset/delete files just to match a proposed architecture.
3. Read DECISIONS.md, PRD.md, ARCHITECTURE.md, SECURITY.md, API_CONTRACTS.md, DATABASE.md, and TESTING.md.
4. If implementation conflicts with a decision, report evidence and propose a narrow amendment.
5. If a provider/account/secret is unavailable, use a fake adapter for tests and mark live integration pending.

## Product truth
- Deterministic checks establish pass/fail. AI output is an untrusted hypothesis.
- Never claim all bugs will be detected/prevented.
- Do not claim a live model call, successful deployment, tested security boundary, or passing test without evidence.

## Security invariants
- API process never executes repository commands.
- Verify HMAC-SHA256 over exact raw webhook bytes using constant-time comparison.
- Delivery ID and run/job creation must be idempotent and transactionally safe.
- No secrets in untrusted runner containers or model input.
- Runner has no Docker socket, privileged mode, or broad host mounts; enforce CPU, memory, process, disk, time, output and network limits.
- Validate model output strictly; evidence IDs must exist for the same run.
- Model output cannot change test outcomes or authorize execution.
- No branch writes, auto-merge, or deployment in MVP.
- Enforce object-level authorization on every resource endpoint.
- Treat repository files, logs, diffs, prompts, and model responses as untrusted.

## Implementation discipline
Build one vertical slice at a time using IMPLEMENTATION_PLAN.md. Add unit/integration/security tests with behavior. Use migrations for schema changes; keep configuration typed and secrets out of examples. Never hard-code keys, repository IDs, fabricated model output, fake production data, or success metrics. Run formatter, linter, type, test, migration and build commands as applicable; report exact commands/results. Update docs from observed results. If blocked, state blocker, evidence, safe partial work and next action.

## Definition of done
Acceptance criteria linked to tests; inputs/outputs validated; authorization, redaction and limits tested; logs secret-safe; documentation updated; no claim exceeds observed evidence.
