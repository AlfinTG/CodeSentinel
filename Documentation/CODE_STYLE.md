# Code Style and Quality Gates

## General
Prefer small, typed, testable modules with explicit dependencies. Keep domain logic separate from HTTP, persistence, provider SDKs and UI. Inject clocks, queues, model clients and process adapters for tests. Document non-obvious security choices and state transitions.

## Python
Python 3.12+; type hints on public functions; Pydantic v2 at external boundaries. Use Ruff for lint/format and pytest; add strict typing checks as configured. Define timeouts and cancellation behavior. Never log tokens, secrets, raw webhook payloads or unredacted provider errors.

## TypeScript
React + TypeScript + Vite with strict compiler options. Use typed API clients and explicit loading/error/empty/partial states. camelCase for variables/functions; PascalCase for components/types. Never use dangerouslySetInnerHTML for model output, code, logs or diffs. Accessible labels, keyboard navigation and visible focus.

## Processes and data
API handlers never execute repository commands. Execute fixed executable paths with argument arrays; never interpolate untrusted strings into a shell. Enforce workspace boundaries, timeout/output/resource limits and cleanup. Never pass secrets to untrusted jobs. Validate external/model input. Use stable API errors. Schema changes require Alembic migration and migration tests.

## Review
Each coherent change includes tests and relevant docs. PR notes state behavior, security impact, exact commands/outcomes and remaining risks. Never report an unrun test as passing.
