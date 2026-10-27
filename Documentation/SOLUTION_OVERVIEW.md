# CodeSentinel — Solution Overview

## One sentence
CodeSentinel turns a GitHub revision into a traceable test result, evidence-backed AI diagnosis, and—after the core path is proven—a safely validated candidate repair.

## Problem and users
A change can break a route or user journey unrelated to the edited file. Developers need to connect a red CI check to an assertion/log, form a hypothesis and choose a next action. The primary user is a solo developer or small team with a GitHub-hosted application and executable tests.

## Differentiator: a verifiable evidence chain
1. Identity: repository, delivery, immutable source revision and run are linked.
2. Observation: isolated configured checks record status, exit code, timing and bounded output.
3. Interpretation: an NVIDIA open-source model served through Nebius Token Factory proposes a diagnosis linked to real evidence IDs.
4. Verification: later, a candidate patch is tested separately. Actual required-check outcomes determine whether it passes.

## Scope
Required first slice: one GitHub repository; verified push/PR webhook; idempotent run; pinned SHA; approved checks; redacted evidence; real model diagnosis; dashboard; honest provider-failure handling.
Later: candidate diff generation, explicit review, isolated re-test, attempt history. No GitHub branch writes, auto-merge or deployment.

## Non-goals
No guarantee all bugs are found; no replacement for CI/monitoring/review; no arbitrary language/framework matrix; no secrets or whole repositories sent to model; no model-generated shell commands; no unmeasured claims about accuracy, bugs prevented or time saved.

## Demo
Show passing baseline → deliberate regression → actual webhook/run → deterministic failed assertion → bounded evidence → live Token Factory model ID and evidence-linked diagnosis → provider failure leaves test status unchanged. Show patch verification only if isolated workflow is implemented and security-gated.
