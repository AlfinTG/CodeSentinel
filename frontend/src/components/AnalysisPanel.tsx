import React from 'react'
import {
  Brain,
  Cpu,
  Clock,
  Sparkles,
  AlertOctagon,
  HelpCircle,
  RefreshCw,
  Link,
  ShieldCheck,
} from 'lucide-react'
import { Analysis, Run } from '../types'
import { StatusBadge } from './StatusBadge'

interface AnalysisPanelProps {
  run: Run
  analysis: Analysis | null
  loading: boolean
  triggering: boolean
  onRequestAnalysis: () => void
  onRetryAnalysis: () => void
  onSelectEvidenceId: (id: string) => void
}

function formatProviderName(provider?: string | null): string {
  if (!provider || provider === 'pending') return 'AI Provider'
  const normalized = provider.toLowerCase().trim()
  if (normalized === 'nvidia' || normalized === 'nvidia_nim') {
    return 'NVIDIA NIM'
  }
  if (normalized === 'nebius' || normalized === 'nebius_token_factory') {
    return 'Nebius Token Factory'
  }
  return provider
}

export const AnalysisPanel: React.FC<AnalysisPanelProps> = ({
  run,
  analysis,
  loading,
  triggering,
  onRequestAnalysis,
  onRetryAnalysis,
  onSelectEvidenceId,
}) => {
  if (loading) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        Loading analysis record...
      </div>
    )
  }

  // Not requested state
  if (!analysis || run.analysis_outcome === 'not_requested') {
    return (
      <div
        style={{
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--bg-border)',
          padding: '36px',
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '16px',
        }}
      >
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            background: 'rgba(99, 102, 241, 0.1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-blue)',
          }}
        >
          <Brain size={24} />
        </div>
        <div>
          <h3 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '6px' }}>
            No Diagnosis Requested Yet
          </h3>
          <p
            style={{
              fontSize: '13px',
              color: 'var(--text-muted)',
              maxWidth: '480px',
              lineHeight: 1.5,
            }}
          >
            Submit this run's bounded evidence to an authorized AI provider for structured root-cause analysis.
          </p>
        </div>
        <button
          onClick={onRequestAnalysis}
          disabled={triggering || run.test_outcome === 'pending'}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: 'var(--accent-blue)',
            color: '#ffffff',
            border: 'none',
            padding: '8px 18px',
            borderRadius: 'var(--radius-sm)',
            fontSize: '13px',
            fontWeight: 600,
            cursor: 'pointer',
            opacity: triggering ? 0.7 : 1,
          }}
        >
          {triggering ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
          <span>{triggering ? 'Queuing Analysis...' : 'Request AI Diagnosis'}</span>
        </button>
      </div>
    )
  }

  // Running state
  if (analysis.status === 'running' || analysis.status === 'pending') {
    return (
      <div
        style={{
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--bg-border)',
          padding: '40px',
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '16px',
        }}
      >
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            background: 'rgba(6, 182, 212, 0.1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-cyan)',
          }}
        >
          <RefreshCw size={24} className="animate-spin" />
        </div>
        <div>
          <h3 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '6px' }}>
            Diagnosis In Progress
          </h3>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', maxWidth: '440px' }}>
            Calling {analysis.model_id}{analysis.provider && analysis.provider !== 'pending' ? ` on ${formatProviderName(analysis.provider)}` : ''}. Analysis state will update automatically once completed.
          </p>
        </div>
      </div>
    )
  }

  // Failed / Unavailable / Invalid Response state
  if (analysis.status === 'failed' || !analysis.result_json) {
    return (
      <div
        style={{
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--analysis-error-border)',
          padding: '32px',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <AlertOctagon size={20} color="var(--analysis-error-text)" />
          <h3 style={{ fontSize: '16px', fontWeight: 600 }}>Diagnosis Unavailable</h3>
        </div>
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
          The AI model service encountered a provider limitation or invalid response schema. Per the core invariant,
          <strong> deterministic test outcomes remain authoritative and unchanged</strong>.
        </p>

        <div
          style={{
            background: 'var(--bg-surface-elevated)',
            padding: '12px 16px',
            borderRadius: 'var(--radius-sm)',
            fontSize: '12px',
            fontFamily: 'var(--font-mono)',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
          }}
        >
          <div>
            <span style={{ color: 'var(--text-subtle)' }}>Safe Error Code: </span>
            <span style={{ color: 'var(--analysis-error-text)' }}>
              {analysis.safe_error_code || 'provider_unavailable'}
            </span>
          </div>
          <div>
            <span style={{ color: 'var(--text-subtle)' }}>Provider: </span>
            <span>{formatProviderName(analysis.provider)}</span>
          </div>
          <div>
            <span style={{ color: 'var(--text-subtle)' }}>Model ID: </span>
            <span>{analysis.model_id}</span>
          </div>
        </div>

        <div>
          <button
            onClick={onRetryAnalysis}
            disabled={triggering}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--bg-border-light)',
              color: 'var(--text-main)',
              padding: '6px 14px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '12px',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            <RefreshCw size={12} className={triggering ? 'animate-spin' : ''} />
            <span>{triggering ? 'Retrying...' : 'Retry Analysis'}</span>
          </button>
        </div>
      </div>
    )
  }

  const diagnosis = analysis.result_json

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Strict Invariant Callout Banner */}
      <div
        style={{
          background: 'rgba(99, 102, 241, 0.08)',
          border: '1px solid rgba(99, 102, 241, 0.25)',
          borderRadius: 'var(--radius-md)',
          padding: '12px 18px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <ShieldCheck size={18} color="var(--analysis-ready-text)" />
          <span style={{ fontSize: '13px', color: 'var(--text-main)' }}>
            <strong>AI Hypotheses:</strong> Grounded strictly in current run's evidence IDs.
            Test results remain the sole factual authority.
          </span>
        </div>

        {/* Retry button */}
        <button
          onClick={onRetryAnalysis}
          disabled={triggering}
          title="Re-run diagnosis against evidence"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
            background: 'var(--bg-surface-elevated)',
            border: '1px solid var(--bg-border)',
            color: 'var(--text-muted)',
            fontSize: '11px',
            padding: '4px 8px',
            borderRadius: 'var(--radius-sm)',
            cursor: 'pointer',
          }}
        >
          <RefreshCw size={11} className={triggering ? 'animate-spin' : ''} />
          <span>Retry</span>
        </button>
      </div>

      {/* Model & Latency Telemetry Card */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px',
          background: 'var(--bg-surface)',
          border: '1px solid var(--bg-border)',
          borderRadius: 'var(--radius-md)',
          padding: '12px 18px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Cpu size={15} color="var(--accent-cyan)" />
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-subtle)' }}>Model ID</div>
            <div style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
              {analysis.model_id}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Clock size={15} color="var(--accent-blue)" />
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-subtle)' }}>Inference Latency</div>
            <div style={{ fontSize: '12px', fontWeight: 600 }}>
              {analysis.latency_ms ? `${(analysis.latency_ms / 1000).toFixed(2)}s` : 'N/A'}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Brain size={15} color="var(--analysis-ready-text)" />
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-subtle)' }}>Provider & Tokens</div>
            <div style={{ fontSize: '12px', fontWeight: 600 }}>
              {formatProviderName(analysis.provider)} ({analysis.input_tokens || 0} in / {analysis.output_tokens || 0} out)
            </div>
          </div>
        </div>
      </div>

      {/* Diagnosis Summary Card */}
      <div
        style={{
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--bg-border)',
          padding: '20px',
        }}
      >
        <h4 style={{ fontSize: '14px', fontWeight: 700, marginBottom: '8px', color: 'var(--accent-cyan)' }}>
          Evidence-Grounded Summary
        </h4>
        <p style={{ fontSize: '14px', lineHeight: 1.6, color: '#f1f5f9' }}>
          {diagnosis.summary}
        </p>
      </div>

      {/* Root Cause Hypotheses */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <h4 style={{ fontSize: '14px', fontWeight: 700 }}>
          Root-Cause Hypotheses ({diagnosis.root_cause_hypotheses.length})
        </h4>

        {diagnosis.root_cause_hypotheses.map((hyp, idx) => (
          <div
            key={idx}
            style={{
              background: 'var(--bg-surface)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--bg-border)',
              padding: '18px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            {/* Top row: statement & confidence */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
              <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-main)', lineHeight: 1.4 }}>
                {hyp.statement}
              </div>
              <StatusBadge type="confidence" value={hyp.confidence} />
            </div>

            {/* Reasoning */}
            <div style={{ fontSize: '13px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              <strong style={{ color: 'var(--text-main)' }}>Reasoning: </strong>
              {hyp.reasoning}
            </div>

            {/* Evidence Links */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', paddingTop: '6px' }}>
              <span style={{ fontSize: '11px', color: 'var(--text-subtle)', display: 'flex', alignItems: 'center', gap: '3px' }}>
                <Link size={11} />
                Cited Evidence:
              </span>
              {hyp.evidence_ids.map((eid) => (
                <button
                  key={eid}
                  onClick={() => onSelectEvidenceId(eid)}
                  title={`Jump to evidence ${eid}`}
                  style={{
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    padding: '2px 8px',
                    borderRadius: 'var(--radius-sm)',
                    background: 'rgba(6, 182, 212, 0.1)',
                    color: 'var(--accent-cyan)',
                    border: '1px solid rgba(6, 182, 212, 0.3)',
                    cursor: 'pointer',
                  }}
                >
                  {eid.substring(0, 8)}...
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Affected Behaviors & Recommended Next Steps */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
        {/* Affected Behaviors */}
        {diagnosis.affected_behaviors.length > 0 && (
          <div
            style={{
              background: 'var(--bg-surface)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--bg-border)',
              padding: '16px',
            }}
          >
            <h5 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '10px', color: 'var(--accent-cyan)' }}>
              Affected Behaviors
            </h5>
            <ul style={{ paddingLeft: '18px', fontSize: '13px', color: 'var(--text-muted)', lineHeight: 1.6 }}>
              {diagnosis.affected_behaviors.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Recommended Next Steps */}
        {diagnosis.recommended_next_steps.length > 0 && (
          <div
            style={{
              background: 'var(--bg-surface)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--bg-border)',
              padding: '16px',
            }}
          >
            <h5 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '10px', color: 'var(--accent-blue)' }}>
              Recommended Next Steps
            </h5>
            <ul style={{ paddingLeft: '18px', fontSize: '13px', color: 'var(--text-muted)', lineHeight: 1.6 }}>
              {diagnosis.recommended_next_steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Missing Evidence */}
      {diagnosis.missing_evidence && diagnosis.missing_evidence.length > 0 && (
        <div
          style={{
            background: 'var(--bg-surface-elevated)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--bg-border)',
            padding: '14px 18px',
            fontSize: '12px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-subtle)', marginBottom: '6px' }}>
            <HelpCircle size={13} />
            <span style={{ fontWeight: 600 }}>Missing Evidence / Unresolved Context</span>
          </div>
          <ul style={{ paddingLeft: '18px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            {diagnosis.missing_evidence.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
