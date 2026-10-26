# Database Model

**Database:** PostgreSQL. Use SQLAlchemy models plus Alembic migrations. The table list below is a domain contract; refine column types/indexes during implementation and keep migration history committed.

## 1. Entities

### `users`
- `id` UUID primary key
- `github_user_id` unique, non-null
- `login` and `display_name` bounded strings
- `created_at`, `updated_at`

### `projects`
- `id` UUID primary key
- `owner_user_id` FK to users
- `github_installation_id` (index; unique where appropriate)
- `repository_id` GitHub numeric ID; do not rely on mutable `owner/name` as identity
- `repository_full_name`, `default_branch`
- `active`, `created_at`, `updated_at`
- Unique constraint for the installation/repository identity according to the actual GitHub App model.

### `project_check_profiles`
- `id`, `project_id` FK
- `name`, `version`, `created_by`, timestamps
- approved check definitions stored as validated structured data or normalized child rows. Commands are allowlisted check identifiers/argument arrays; never free-form model output.
- Configuration changes create a new version so a run can record the exact profile used.

### `github_deliveries`
- `id` UUID
- `delivery_id` unique (GitHub `X-GitHub-Delivery`)
- `event_name`, `installation_id`, `repository_id`
- `payload_digest` (optional, never store entire raw payload by default)
- `received_at`, `processing_status`, `linked_run_id` nullable FK
- Store minimal metadata only; raw webhook payload retention should be avoided unless a reviewed debugging need exists.

### `runs`
- `id`, `project_id` FK
- `delivery_id` FK/unique relationship where the event produces one logical run
- `event_kind`, `pr_number` nullable, `base_sha` nullable, `head_sha` pinned/non-null for executable runs
- `check_profile_version` reference/snapshot
- `lifecycle_status`, `test_outcome`, `analysis_outcome`
- `queued_at`, `started_at`, `completed_at`, `created_at`, `updated_at`
- `attempt_number`, `correlation_id`, `failure_code` nullable
- Indexes: `(project_id, created_at desc)`, `(lifecycle_status, queued_at)`, `(head_sha)`.

### `check_runs`
- `id`, `run_id` FK
- `check_key`, `check_name`, `profile_version`
- `status`, `exit_code` nullable, `started_at`, `completed_at`, `duration_ms` nullable
- `output_truncated`, `failure_code` nullable, `attempt_number`
- Unique `(run_id, check_key, attempt_number)`.

### `evidence_items`
- `id`, `run_id` FK, `check_run_id` nullable FK
- `kind` enum/string allowlist (failed_assertion, stack_trace, test_report, log_excerpt, route_result, diff_excerpt)
- `source_label`, `content_redacted`, `content_digest`, `truncated`, `byte_length`, `created_at`
- Enforce size limits before insert; avoid raw secrets or entire repositories.

### `analyses`
- `id`, `run_id` FK, `attempt_number`
- `provider`, `model_id`, `status`, `schema_version`
- `result_json` validated structure or null
- `input_digest`, `started_at`, `completed_at`, `latency_ms`, `input_tokens`/`output_tokens` nullable only if provider returns them
- `safe_error_code` nullable; do not persist unredacted raw provider response on schema failure.
- Unique `(run_id, attempt_number)`.

### P1 `patch_attempts`
- `id`, `run_id` FK, `requested_by` FK
- `status`, `base_sha`, `candidate_digest`, `workspace_reference` (opaque and short-lived), `created_at`, `validated_at`, `expires_at`
- Never store credentials in workspace references. Candidate content access requires authorization.

### `audit_events` (minimal)
- `id`, actor user or system principal, project ID, action enum, target type/ID, timestamp, safe metadata JSON
- Do not put prompt contents, secrets, or unredacted code in audit metadata.

## 2. Integrity requirements

- Use FK constraints for ownership and parent-child records.
- Unique delivery ID is the first idempotency barrier; DB transaction also prevents duplicate run/enqueue records.
- Store immutable commit identifiers and check profile version on the run.
- State changes occur through service functions with legal transition checks, not arbitrary UI/API updates.
- Use optimistic version/attempt fields where concurrent workers could race.
- All user-facing queries must constrain records through authorized project ownership/installation. UUIDs do not replace authorization.
- Alembic migrations must be tested up from an empty database and from the previous migration.

## 3. Retention and privacy

Initial demo policy: keep run metadata and redacted evidence for 30 days, unless an explicit project setting says otherwise. Define cleanup jobs, cascade behavior, and user-visible unlink/delete semantics before public release. Do not persist full repositories, environment variables, raw credentials, or complete webhook payloads by default. External model submissions should be bounded and documented.

## 4. Storage limits (initial defaults)

- Evidence item: 256 KiB maximum persisted content per check output bundle, with smaller UI pages.
- Diff/log excerpts in a model request: configurable caps and total token budget based on chosen model context; preserve truncation markers.
- Structured JSON: validate before persistence; reject oversized/unknown fields or store only a safe error code.

These are initial implementation limits, not benchmarked capacity figures.
