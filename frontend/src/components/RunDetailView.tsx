import React, { useState } from 'react'
import {
  GitCommit,
  GitPullRequest,
  CheckCircle2,
  XCircle,
  Copy,
  Check,
  Ban,
  Clock,
  Terminal,
  Brain,
  Wrench,
} from 'lucide-react'
import { RunDetail, CheckRun, EvidenceItem, Analysis, PatchAttempt } from '../types'
import { StatusBadge } from './StatusBadge'
import { EvidenceViewer } from './EvidenceViewer'
import { AnalysisPanel } from './AnalysisPanel'
import { PatchView } from './PatchView'

interface RunDetailViewProps {
  run: RunDetail
  checks: CheckRun[]
  evidence: EvidenceItem[]
  analysis: Analysis | null
  patchAttempts: PatchAttempt[]
  loadingDetail: boolean
  triggeringAnalysis: boolean
  triggeringPatch: boolean
  cancellingRun: boolean
  onRequestAnalysis: () => void
  onRetryAnalysis: () => void
  onProposePatch: (instructions?: string) => void
  onCancelRun: () => void
}

export const RunDetailView: React.FC<RunDetailViewProps> = ({
  run,
  checks,
  evidence,
  analysis,
  patchAttempts,
  loadingDetail,
  triggeringAnalysis,
  triggeringPatch,
  cancellingRun,
  onRequestAnalysis,
  onRetryAnalysis,
  onProposePatch,
  onCancelRun,
}) => {
  const [activeTab, setActiveTab] = useState<'evidence' | 'analysis' | 'patch'>('evidence')
  const [highlightedEvidenceId, setHighlightedEvidenceId] = useState<string | null>(null)
  const [copiedSha, setCopiedSha] = useState<boolean>(false)

  const copySha = () => {
    navigator.clipboard.writeText(run.head_sha)
    setCopiedSha(true)
    setTimeout(() => setCopiedSha(false), 1500)
  }

  const handleSelectEvidenceId = (evidenceId: string) => {
    setActiveTab('evidence')
    setHighlightedEvidenceId(evidenceId)
    setTimeout(() => {
      const el = document.getElementById(`evidence-${evidenceId}`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }, 100)
  }

  const canCancel =
    run.lifecycle_status === 'running' ||
    run.lifecycle_status === 'queued' ||
    run.lifecycle_status === 'received'

  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--bg-border)',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Run Header */}
      <div
        style={{
          padding: '20px 24px',
          borderBottom: '1px solid var(--bg-border)',
          background: 'var(--bg-surface-elevated)',
          display: 'flex',
          flexDirection: 'column',
          gap: '14px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
              <span style={{ fontSize: '18px', fontWeight: 700, letterSpacing: '-0.01em' }}>
                Run {run.id.substring(0, 8)}
              </span>
              <StatusBadge type="test" value={run.test_outcome} size="md" />
              <StatusBadge type="analysis" value={run.analysis_outcome} size="md" />
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '12px', color: 'var(--text-muted)', flexWrap: 'wrap' }}>
              {/* Commit SHA */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', fontFamily: 'var(--font-mono)' }}>
                <GitCommit size={14} color="var(--accent-cyan)" />
                <span style={{ color: 'var(--text-main)', fontWeight: 600 }}>{run.head_sha.substring(0, 7)}</span>
                <button
                  onClick={copySha}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: copiedSha ? 'var(--status-pass-text)' : 'var(--text-subtle)',
                    cursor: 'pointer',
                    padding: '2px',
                  }}
                >
                  {copiedSha ? <Check size={12} /> : <Copy size={12} />}
                </button>
              </div>

              {/* Event / PR */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                {run.event_kind === 'pull_request' ? (
                  <>
                    <GitPullRequest size={13} color="var(--accent-blue)" />
                    <span>PR #{run.pr_number || '42'}</span>
                  </>
                ) : (
                  <span>push</span>
                )}
              </div>

              <span>Profile v{run.check_profile_version}</span>
              <span>Attempt #{run.attempt_number}</span>
            </div>
          </div>

          {/* Action buttons */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {canCancel && (
              <button
                onClick={onCancelRun}
                disabled={cancellingRun}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  background: 'rgba(239, 68, 68, 0.1)',
                  border: '1px solid rgba(239, 68, 68, 0.3)',
                  color: 'var(--status-fail-text)',
                  fontSize: '12px',
                  padding: '6px 12px',
                  borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer',
                }}
              >
                <Ban size={13} />
                <span>{cancellingRun ? 'Cancelling...' : 'Cancel Run'}</span>
              </button>
            )}
          </div>
        </div>

        {/* Configured Checks Snapshot Bar */}
        {checks.length > 0 && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
              paddingTop: '10px',
              borderTop: '1px solid rgba(255, 255, 255, 0.05)',
            }}
          >
            <span style={{ fontSize: '11px', color: 'var(--text-subtle)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Executed Checks
            </span>
            <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
              {checks.map((chk) => (
                <div
                  key={chk.id}
                  style={{
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--bg-border)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '6px 10px',
                    fontSize: '12px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                  }}
                >
                  {chk.status === 'passed' ? (
                    <CheckCircle2 size={13} color="var(--status-pass-text)" />
                  ) : chk.status === 'failed' ? (
                    <XCircle size={13} color="var(--status-fail-text)" />
                  ) : (
                    <Clock size={13} color="var(--accent-blue)" />
                  )}
                  <span style={{ fontWeight: 600 }}>{chk.check_name}</span>
                  <span style={{ color: 'var(--text-subtle)', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                    exit {chk.exit_code !== null ? chk.exit_code : '?'} &middot;{' '}
                    {chk.duration_ms ? `${(chk.duration_ms / 1000).toFixed(1)}s` : 'running'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* View Tabs */}
      <div
        style={{
          borderBottom: '1px solid var(--bg-border)',
          display: 'flex',
          background: 'var(--bg-surface)',
          padding: '0 24px',
          gap: '24px',
        }}
      >
        <button
          onClick={() => setActiveTab('evidence')}
          style={{
            background: 'transparent',
            border: 'none',
            padding: '14px 4px',
            fontSize: '13px',
            fontWeight: 600,
            cursor: 'pointer',
            color: activeTab === 'evidence' ? 'var(--accent-cyan)' : 'var(--text-muted)',
            borderBottom: activeTab === 'evidence' ? '2px solid var(--accent-cyan)' : '2px solid transparent',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <Terminal size={15} />
          <span>Factual Checks &amp; Evidence ({evidence.length})</span>
        </button>

        <button
          onClick={() => setActiveTab('analysis')}
          style={{
            background: 'transparent',
            border: 'none',
            padding: '14px 4px',
            fontSize: '13px',
            fontWeight: 600,
            cursor: 'pointer',
            color: activeTab === 'analysis' ? 'var(--accent-blue)' : 'var(--text-muted)',
            borderBottom: activeTab === 'analysis' ? '2px solid var(--accent-blue)' : '2px solid transparent',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <Brain size={15} />
          <span>AI Regression Diagnosis</span>
          {analysis?.status === 'succeeded' && (
            <span
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: 'var(--accent-blue)',
              }}
            />
          )}
        </button>

        <button
          onClick={() => setActiveTab('patch')}
          style={{
            background: 'transparent',
            border: 'none',
            padding: '14px 4px',
            fontSize: '13px',
            fontWeight: 600,
            cursor: 'pointer',
            color: activeTab === 'patch' ? 'var(--status-pass-text)' : 'var(--text-muted)',
            borderBottom: activeTab === 'patch' ? '2px solid var(--status-pass-text)' : '2px solid transparent',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <Wrench size={15} />
          <span>Candidate Patch (P1)</span>
          {patchAttempts.length > 0 && (
            <span
              style={{
                fontSize: '11px',
                padding: '1px 6px',
                borderRadius: '10px',
                background: 'rgba(52, 211, 153, 0.15)',
                color: 'var(--status-pass-text)',
              }}
            >
              {patchAttempts.length}
            </span>
          )}
        </button>
      </div>

      {/* Tab Content Body */}
      <div style={{ padding: '24px' }}>
        {activeTab === 'evidence' ? (
          <EvidenceViewer
            evidenceItems={evidence}
            loading={loadingDetail}
            highlightedEvidenceId={highlightedEvidenceId}
            onClearHighlight={() => setHighlightedEvidenceId(null)}
          />
        ) : activeTab === 'analysis' ? (
          <AnalysisPanel
            run={run}
            analysis={analysis}
            loading={loadingDetail}
            triggering={triggeringAnalysis}
            onRequestAnalysis={onRequestAnalysis}
            onRetryAnalysis={onRetryAnalysis}
            onSelectEvidenceId={handleSelectEvidenceId}
          />
        ) : (
          <PatchView
            run={run}
            patchAttempts={patchAttempts}
            loading={loadingDetail}
            triggering={triggeringPatch}
            onProposePatch={onProposePatch}
          />
        )}
      </div>
    </div>
  )
}
