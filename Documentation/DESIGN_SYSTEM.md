# Design System

## Intent
Restrained, high-contrast developer interface prioritizing source revision, test facts and evidence over decorative AI output. Clear hierarchy, concise copy, predictable interactions and responsive layout.

## Information architecture
1. Project overview and integration status.
2. Run history with commit, trigger, time and distinct test/analysis states.
3. Run detail: pinned SHA, configured checks, factual result, evidence, diagnosis and attempts.
4. Settings: repository connection, check profiles, retention and integration health.

## Status language
Test: Passed, Failed, Timed out, Cancelled, Inconclusive, Error. Analysis: Pending, Running, Diagnosis available, Unavailable, Invalid response. Patch (P1): Proposed, Validating, Verified, Failed, Timed out, Unverified. Never use green/pass for model confidence. A diagnosis is not a test result.

## Interaction requirements
Loading, empty, denied, offline, timeout, partial and error states. Keyboard-accessible controls and visible focus. Status is expressed in text/icon as well as color. Logs, diffs and model-generated content render as escaped plain text. Preserve copyable commit SHA, evidence IDs, timestamps and safe error codes. Bound logs with pagination and truncation notices.

## Visual direction
Neutral surfaces, restrained accent, readable typography, consistent spacing, clear table alignment. Avoid “AI magic” effects suggesting certainty. Aim for WCAG 2.2 AA contrast and keyboard behavior; verify with automated and manual tests.
