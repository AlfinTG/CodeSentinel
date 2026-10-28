export interface HealthState {
  status: string
  database?: string
  queue?: string
}

export interface ProjectCheckProfile {
  id: string
  name: string
  version: number
  check_definitions: Array<{
    key: string
    name: string
    command: string[]
    timeout_seconds?: number
  }>
  created_at: string
}

export interface Project {
  id: string
  repository: string
  default_branch: string
  created_at: string
}

export interface ProjectDetail extends Project {
  active: boolean
  updated_at: string
  active_profile?: ProjectCheckProfile | null
  integration_health: string
}

export type LifecycleStatus =
  | 'received'
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'timed_out'
  | 'cancelled'
  | 'inconclusive'

export type TestOutcome =
  | 'pending'
  | 'passed'
  | 'failed'
  | 'timed_out'
  | 'cancelled'
  | 'inconclusive'
  | 'error'

export type AnalysisOutcome =
  | 'not_requested'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'unavailable'
  | 'invalid_response'

export interface Run {
  id: string
  project_id: string
  delivery_id?: string | null
  event_kind: string
  pr_number?: number | null
  base_sha?: string | null
  head_sha: string
  check_profile_version: number
  lifecycle_status: LifecycleStatus
  test_outcome: TestOutcome
  analysis_outcome: AnalysisOutcome
  attempt_number: number
  correlation_id: string
  failure_code?: string | null
  queued_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  created_at: string
}

export interface CheckRun {
  id: string
  run_id: string
  check_key: string
  check_name: string
  profile_version: number
  status: string
  exit_code?: number | null
  started_at?: string | null
  completed_at?: string | null
  duration_ms?: number | null
  output_truncated: boolean
  failure_code?: string | null
  attempt_number: number
}

export interface RunDetail extends Run {
  checks: CheckRun[]
}

export type EvidenceKind =
  | 'failed_assertion'
  | 'stack_trace'
  | 'test_report'
  | 'log_excerpt'
  | 'route_result'
  | 'diff_excerpt'

export interface EvidenceItem {
  id: string
  run_id: string
  check_run_id?: string | null
  kind: EvidenceKind
  source_label: string
  content_redacted: string
  content_digest: string
  truncated: boolean
  byte_length: number
  created_at: string
}

export interface RootCauseHypothesis {
  statement: string
  evidence_ids: string[]
  confidence: 'low' | 'medium' | 'high'
  reasoning: string
}

export interface ModelDiagnosisSchema {
  summary: string
  root_cause_hypotheses: RootCauseHypothesis[]
  affected_behaviors: string[]
  recommended_next_steps: string[]
  missing_evidence: string[]
}

export interface Analysis {
  id: string
  run_id: string
  attempt_number: number
  provider: string
  model_id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  schema_version: string
  result_json?: ModelDiagnosisSchema | null
  input_digest: string
  started_at?: string | null
  completed_at?: string | null
  latency_ms?: number | null
  input_tokens?: number | null
  output_tokens?: number | null
  safe_error_code?: string | null
}

export interface ApiError {
  code: string
  message: string
  request_id?: string
}

export type PatchStatus =
  | 'proposed'
  | 'validating'
  | 'verified'
  | 'failed'
  | 'timed_out'
  | 'unverified'

export interface PatchValidationCheck {
  check_name: string
  status: string
  exit_code?: number | null
  duration_ms: number
  failure_code?: string | null
}

export interface PatchValidationSummary {
  all_passed: boolean
  checks: PatchValidationCheck[]
  failure_reason?: string | null
}

export interface PatchAttempt {
  id: string
  run_id: string
  requested_by_user_id: string
  status: PatchStatus
  base_sha: string
  candidate_digest: string
  workspace_reference: string
  summary?: string | null
  diff_content?: string | null
  validation_results?: PatchValidationSummary | null
  validated_at?: string | null
  expires_at?: string | null
  created_at: string
}

