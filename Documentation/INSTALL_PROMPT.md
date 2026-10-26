# Environment Setup Agent Prompt

Inspect repository state before changing anything. Read AGENTS.md, DECISIONS.md and IMPLEMENTATION_PLAN.md. Prepare a reproducible local environment for the current implemented phase only.

- Pin versions and use lockfiles; never leave final setup on unpinned latest dependencies.
- Use placeholder-only .env.example; never request, print, commit or log real credentials.
- Start PostgreSQL/Redis, run migrations and report exact results.
- Keep API and worker separate; do not enable untrusted repository execution before its security gate.
- Run documented formatter/lint/type/test/build commands and report exact outcomes.
- If external account/model is missing, use a fake adapter for tests and mark live verification pending.
- Do not claim reproducibility until clean setup succeeds.
