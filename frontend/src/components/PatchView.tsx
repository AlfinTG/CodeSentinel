import React, { useState } from 'react'
import {
  Wrench,
  CheckCircle2,
  XCircle,
  Shield,
  Sparkles,
  RefreshCw,
  Terminal,
} from 'lucide-react'
import { PatchAttempt, Run } from '../types'
import { DiffViewer } from './DiffViewer'

interface PatchViewProps {
  run: Run
  patchAttempts: PatchAttempt[]
  loading: boolean
  triggering: boolean
  onProposePatch: (instructions?: string) => void
}

export const PatchView: React.FC<PatchViewProps> = ({
  run,
  patchAttempts,
  loading,
  triggering,
  onProposePatch,
}) => {
  const [instructions, setInstructions] = useState<string>('')
  const [selectedAttemptId, setSelectedAttemptId] = useState<string | null>(null)

  const activeAttempt =
    patchAttempts.find((a) => a.id === selectedAttemptId) || patchAttempts[0] || null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }} role="region" aria-label="Candidate Patch Verification">
      {/* Strict Security Invariant Callout */}
      <div
        style={{
          background: 'rgba(245, 158, 11, 0.08)',
          border: '1px solid rgba(245, 158, 11, 0.25)',
          borderRadius: 'var(--radius-md)',
          padding: '12px 18px',
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
        }}
        role="alert"
      >
        <Shield size={20} color="var(--status-timeout-text)" />
        <div style={{ fontSize: '13px', color: 'var(--text-main)', lineHeight: 1.4 }}>
          <strong>P1 Security Boundary:</strong> Candidate patches are tested exclusively in ephemeral,
          isolated sandboxes. CodeSentinel <strong>never writes to GitHub branches, opens pull requests,
          auto-merges, or deploys code</strong>.
        </div>
      </div>

      {/* Propose Action Box */}
      <div
        style={{
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--bg-border)',
          padding: '20px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Wrench size={16} color="var(--accent-blue)" />
          <h4 style={{ fontSize: '14px', fontWeight: 700 }}>
            Propose &amp; Verify Candidate Repair
          </h4>
        </div>
        <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
          Generate a candidate repair diff based on the regression evidence and hypothesis, apply it in an isolated ephemeral workspace, and re-run required checks.
        </p>

        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="Optional instructions (e.g. 'Preserve coupon type signature')"
            aria-label="Patch guidance instructions"
            style={{
              flex: 1,
              minWidth: '240px',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-sm)',
              padding: '8px 12px',
              color: 'var(--text-main)',
              fontSize: '13px',
              outline: 'none',
            }}
          />
          <button
            onClick={() => onProposePatch(instructions)}
            disabled={triggering || run.test_outcome === 'pending'}
            aria-label="Propose and verify candidate patch"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              background: 'linear-gradient(135deg, #0284c7, #2563eb)',
              color: '#ffffff',
              border: 'none',
              padding: '8px 16px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '13px',
              fontWeight: 600,
              cursor: 'pointer',
              opacity: triggering ? 0.7 : 1,
            }}
          >
            {triggering ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <Sparkles size={14} />
            )}
            <span>{triggering ? 'Validating Patch in Sandbox...' : 'Propose & Verify Patch'}</span>
          </button>
        </div>
      </div>

      {/* Attempts History */}
      {loading ? (
        <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }} role="status">
          Loading candidate patch attempts...
        </div>
      ) : patchAttempts.length === 0 ? (
        <div
          style={{
            padding: '36px',
            textAlign: 'center',
            color: 'var(--text-muted)',
            background: 'var(--bg-surface)',
            borderRadius: 'var(--radius-lg)',
            border: '1px solid var(--bg-border)',
          }}
        >
          No candidate patches proposed for this run yet. Click "Propose &amp; Verify Patch" above to test an isolated repair.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* Attempts Selector Tabs if multiple */}
          {patchAttempts.length > 1 && (
            <div style={{ display: 'flex', gap: '8px', overflowX: 'auto' }} role="tablist" aria-label="Patch attempt versions">
              {patchAttempts.map((att, idx) => {
                const isSelected = activeAttempt?.id === att.id
                return (
                  <button
                    key={att.id}
                    onClick={() => setSelectedAttemptId(att.id)}
                    role="tab"
                    aria-selected={isSelected}
                    aria-label={`Attempt ${patchAttempts.length - idx}, status ${att.status}`}
                    style={{
                      padding: '6px 12px',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '12px',
                      fontWeight: 600,
                      cursor: 'pointer',
                      background: isSelected ? 'var(--bg-surface-elevated)' : 'transparent',
                      border: isSelected ? '1px solid var(--accent-cyan)' : '1px solid var(--bg-border)',
                      color: isSelected ? 'var(--accent-cyan)' : 'var(--text-muted)',
                    }}
                  >
                    Attempt #{patchAttempts.length - idx} ({att.status})
                  </button>
                )
              })}
            </div>
          )}

          {/* Active Attempt Details */}
          {activeAttempt && (
            <div
              style={{
                background: 'var(--bg-surface)',
                borderRadius: 'var(--radius-lg)',
                border: '1px solid var(--bg-border)',
                overflow: 'hidden',
                display: 'flex',
                flexDirection: 'column',
                gap: '16px',
              }}
            >
              {/* Header Status Bar */}
              <div
                style={{
                  padding: '14px 18px',
                  background: 'var(--bg-surface-elevated)',
                  borderBottom: '1px solid var(--bg-border)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexWrap: 'wrap',
                  gap: '10px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      padding: '3px 8px',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '11px',
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      background:
                        activeAttempt.status === 'verified'
                          ? 'var(--status-pass-bg)'
                          : 'var(--status-fail-bg)',
                      color:
                        activeAttempt.status === 'verified'
                          ? 'var(--status-pass-text)'
                          : 'var(--status-fail-text)',
                      border: `1px solid ${
                        activeAttempt.status === 'verified'
                          ? 'var(--status-pass-border)'
                          : 'var(--status-fail-border)'
                      }`,
                    }}
                  >
                    {activeAttempt.status === 'verified' ? (
                      <CheckCircle2 size={12} />
                    ) : (
                      <XCircle size={12} />
                    )}
                    <span>{activeAttempt.status}</span>
                  </span>

                  <span style={{ fontSize: '13px', fontWeight: 600 }}>
                    {activeAttempt.summary || 'Candidate patch'}
                  </span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-subtle)' }}>
                    Digest: {activeAttempt.candidate_digest.substring(0, 8)} &middot;{' '}
                    {activeAttempt.workspace_reference}
                  </span>
                </div>
              </div>

              {/* Advanced Syntax-Highlighted Diff Viewer with Unified/Split View */}
              {activeAttempt.diff_content ? (
                <div style={{ padding: '0 16px' }}>
                  <DiffViewer
                    diffText={activeAttempt.diff_content}
                    title={activeAttempt.summary || `Candidate Patch Diff (${activeAttempt.candidate_digest.substring(0, 8)})`}
                    initialMode="unified"
                  />
                </div>
              ) : (
                <div style={{ padding: '24px', color: 'var(--text-muted)', fontSize: '13px', textAlign: 'center' }}>
                  No diff content recorded for this patch attempt.
                </div>
              )}

              {/* Isolated Verification Results */}
              {activeAttempt.validation_results && (
                <div style={{ padding: '0 20px 20px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Terminal size={14} color="var(--accent-cyan)" />
                    <span style={{ fontSize: '13px', fontWeight: 700 }}>
                      Isolated Sandbox Check Results
                    </span>
                  </div>

                  <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                    {activeAttempt.validation_results.checks.map((chk, i) => (
                      <div
                        key={i}
                        style={{
                          background: 'var(--bg-surface-elevated)',
                          border: '1px solid var(--bg-border)',
                          borderRadius: 'var(--radius-sm)',
                          padding: '8px 12px',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          fontSize: '12px',
                        }}
                      >
                        {chk.status === 'passed' ? (
                          <CheckCircle2 size={14} color="var(--status-pass-text)" />
                        ) : (
                          <XCircle size={14} color="var(--status-fail-text)" />
                        )}
                        <span style={{ fontWeight: 600 }}>{chk.check_name}</span>
                        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-subtle)', fontSize: '11px' }}>
                          exit {chk.exit_code !== null ? chk.exit_code : '?'} &middot;{' '}
                          {(chk.duration_ms / 1000).toFixed(1)}s
                        </span>
                      </div>
                    ))}
                  </div>

                  {activeAttempt.validation_results.failure_reason && (
                    <div style={{ color: 'var(--status-fail-text)', fontSize: '12px', fontStyle: 'italic', marginTop: '4px' }}>
                      Verification Note: {activeAttempt.validation_results.failure_reason}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
