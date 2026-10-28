# Implementation Agent Prompt

You are implementing CodeSentinel from the repository's approved specification. Begin by inspecting the actual repository state and reading `AGENTS.md`, `DECISIONS.md`, `PRD.md`, `ARCHITECTURE.md`, `SECURITY.md`, `API_CONTRACTS.md`, `DATABASE.md`, `TESTING.md`, and `IMPLEMENTATION_PLAN.md`.

## Mission

Build the smallest complete, trustworthy vertical slice for one GitHub repository: signed/idempotent GitHub event → pinned revision → isolated configured checks → redacted evidence → live NVIDIA open-source model diagnosis through Nebius Token Factory → UI that shows factual test outcome separately from analysis outcome.

Candidate patch generation/validation is phase 2 and must not be implemented ahead of the required security and evidence gates.

## Non-negotiable rules

- Do not assume documentation means code already exists; inspect first.
- Preserve uncommitted work and existing conventions where valid.
- Do not fabricate provider responses, test outcomes, metrics, or deployment status.
- Do not execute repository code in the API process.
- Do not execute arbitrary model/client-provided commands.
- Do not expose secrets to the browser, database logs, prompts, or untrusted runner.
- Do not give untrusted execution containers Docker sockets, privileged mode, or broad host mounts.
- Implement HMAC-SHA256 raw-body verification and delivery-id idempotency.
- Use durable DB state and clear transitions; test and analysis statuses must be independent.
- Validate all model output and reject evidence IDs that are not part of the current run.
- No protected branch writes, auto-merge, or production deployment.
- Follow `IMPLEMENTATION_PLAN.md` gates in sequence; do not skip gates to add UI polish.

## Execution method

1. Report current repo tree/stack, git status, and any conflicts with the decisions.
2. Work on one phase from `IMPLEMENTATION_PLAN.md` only. State the goal and acceptance criteria.
3. Implement code and automated tests together; use fakes for unavailable external services, but clearly mark the live integration as pending.
4. Run the exact relevant formatter, lint, type-check, test, migration, and build commands.
5. Inspect failures and fix them rather than hiding or skipping relevant tests.
6. Update relevant docs and status table based on observed results.
7. Summarize files changed, commands run, actual outcomes, security implications, remaining risks and the next gate.

## First task

Complete Phase 0 and only the beginning of Phase 1 as time permits. Do not create a large scaffold without first confirming the actual repository and chosen baseline. If stack/credential/hosting decisions materially block work, list the exact blocked decision with evidence and proceed with a non-secret, testable partial slice.
