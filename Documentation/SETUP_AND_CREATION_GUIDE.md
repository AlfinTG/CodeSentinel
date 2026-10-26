# Setup and Creation Guide

## Before implementation
1. Clone and inspect branch/status/tree; preserve current work.
2. Read decisions, implementation plan, API/database/security/testing specifications.
3. Confirm GitHub App, Nebius account/model access and budget without committing credentials.
4. Owner chooses a license.
5. Assign a Phase 0 owner and record external blockers.

## Suggested layout
- frontend/: React + TypeScript + Vite
- backend/: FastAPI, domain services, API and migrations
- runner/: trusted supervisor/runner interfaces as justified
- .env.example: placeholders only
- docker-compose.yml

Adjust to actual code; do not create empty abstractions solely to match a diagram.

## Local setup
Pin runtime/dependency versions and commit lockfiles. Start PostgreSQL/Redis; migrate before starting API; run API and worker separately. Keep untrusted execution disabled until Gate 3 passes.

## Each phase report
Include gate, changed files, tests added, exact commands/outcomes, security implications, blockers and next action. Update README only from observed results.

## Clean environment validation
Install from lockfiles, start dependencies, migrate empty DB, run tests/build, start UI/API/worker, execute controlled regression, scan for secrets, and check secret canaries do not appear in logs/database/model request.

## Release
Use RELEASE_CHECKLIST.md. If runner isolation is unproven, disable external repository execution and demo controlled fixtures in a trusted environment.
