# Product Requirements Document

## Objective
Build a trustworthy developer tool that reduces time required to understand a regression by connecting a GitHub source revision to deterministic checks, bounded evidence and a structured AI diagnosis.

## Primary user
A developer or small team maintaining one GitHub repository with reproducible tests. They need to know what failed on a specific commit, inspect evidence, and evaluate plausible causes without confusing AI guesses with facts.

## MVP scope (P0)
1. GitHub App connection and repository authorization.
2. Verify raw-body webhook HMAC and filter supported push/PR events.
3. Deduplicate deliveries and create one durable run per accepted event.
4. Pin immutable commit SHA and versioned check profile.
5. Run only approved check profiles in isolated, bounded worker containers.
6. Persist factual status and redacted/bounded evidence.
7. Call a configured eligible NVIDIA open model through Nebius Token Factory.
8. Validate structured diagnosis and evidence IDs; preserve test outcome if model fails.
9. Dashboard shows project, runs, checks, evidence, diagnosis and partial/error states.
10. Reproducible setup, tests, public demo, security and release checklist.

## P1
Candidate patch proposal and isolated validation; clear diff; required checks; per-attempt results. No auto-merge/deploy.

## Out of scope
Hardened multi-tenant service, arbitrary command execution, broad language matrix, analytics claims without measurements, autonomous production writes.

## Product rules
- Test outcome is pass, fail, timed out, cancelled, error, or inconclusive.
- Analysis state is independent: pending, running, succeeded, unavailable, invalid response.
- Missing/partial evidence never becomes a pass.
- Model hypotheses cite evidence IDs from this run and remain labeled hypotheses.
- Only explicit policy-approved check profiles may execute.

## Acceptance criteria
Valid signature accepted; invalid signature rejected; concurrent duplicate delivery creates one logical run/job; run records immutable revision and check-profile version; runner cannot access secrets/socket/host filesystem/network by default; deliberate regression creates repeatable failure and visible evidence; live model response is schema-validated and cites existing evidence; provider timeout/invalid JSON leaves test outcome unchanged; cross-project access denied; clean setup/migration/tests/UI build succeed; demo claims match observed behavior.
