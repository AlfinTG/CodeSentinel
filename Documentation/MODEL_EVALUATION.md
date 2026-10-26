# Model Evaluation Plan

## Goal
Measure whether the configured NVIDIA open model served through Nebius Token Factory produces useful evidence-grounded regression diagnoses without treating model output as truth.

## Evaluation set
Build a versioned fixture set of controlled regressions across supported sample-app behaviors. For each case, record pinned commit, expected failing check, evidence bundle, maintainer-written plausible causes, relevant evidence IDs and known ambiguities. Keep a passing control and adversarial prompt-injection examples.

## Output contract
Validate against API_CONTRACTS.md: concise summary; root-cause hypotheses with evidence IDs, qualitative confidence and rationale; affected behaviors; safe next steps; missing evidence. Reject invalid JSON, oversized content and evidence IDs not belonging to the run.

## Metrics to report
Schema-valid response rate; evidence-reference validity rate; maintainer-rated usefulness/grounding; unsupported-claim rate; provider latency/failure and cost fields when available; repeatability across calls with model ID, prompt/schema version and date.

Do not invent targets/results or call qualitative confidence a calibrated probability. Report sample size, fixture selection and method. Compare model/prompt changes on the same frozen set.

## Failure behavior
Timeout, rate limit, provider outage and invalid response produce an analysis error state while preserving deterministic test outcome. Use fakes in ordinary CI; live smoke tests are opt-in and secrets never go to public CI.
