import React, { useState } from 'react'
import {
  Wrench,
  CheckCircle2,
  XCircle,
  Copy,
  Check,
  Shield,
  Sparkles,
  RefreshCw,
  Terminal,
} from 'lucide-react'
import { PatchAttempt, Run } from '../types'

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
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [selectedAttemptId, setSelectedAttemptId] = useState<string | null>(null)

  const copyDiff = (id: string, text: string) => {
    navigator.clipboard.writeText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 1500)
  }

  const activeAttempt =
    patchAttempts.find((a) => a.id === selectedAttemptId) || patchAttempts[0] || null

  const renderDiffLine = (line: string, index: number) => {
    let bg = 'transparent'
    let color = '#e2e8f0'

    if (line.startsWith('+++') || line.startsWith('---')) {
      color = 'var(--accent-cyan)'
      bg = 'rgba(6, 182, 212, 0.08)'
    } else if (line.startsWith('@@')) {
      color = 'var(--text-subtle)'
      bg = 'rgba(255, 255, 255, 0.03)'
    } else if (line.startsWith('+')) {
      color = '#34d399'
      bg = 'rgba(16, 185, 129, 0.12)'
    } else if (line.startsWith('-')) {
      color = '#f87171'
      bg = 'rgba(239, 68, 68, 0.12)'
    }

    return (
      <div
        key={index}
        style={{
          background: bg,
          color,
          padding: '1px 8px',
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          lineHeight: 1.5,
          whiteSpace: 'pre',
        }}
      >
        {line}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
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
        <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
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
            <div style={{ display: 'flex', gap: '8px', overflowX: 'auto' }}>
              {patchAttempts.map((att, idx) => (
                <button
                  key={att.id}
                  onClick={() => setSelectedAttemptId(att.id)}
                  style={{
                    padding: '6px 12px',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '12px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    background:
                      (activeAttempt?.id === att.id)
                        ? 'var(--bg-surface-elevated)'
                        : 'transparent',
                    border:
                      (activeAttempt?.id === att.id)
                        ? '1px solid var(--accent-cyan)'
                        : '1px solid var(--bg-border)',
                    color:
                      (activeAttempt?.id === att.id)
                        ? 'var(--accent-cyan)'
                        : 'var(--text-muted)',
                  }}
                >
                  Attempt #{patchAttempts.length - idx} ({att.status})
                </button>
              ))}
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
              }}
            >
              {/* Header */}
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
                  <button
                    onClick={() => copyDiff(activeAttempt.id, activeAttempt.diff_content || '')}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px',
                      background: 'transparent',
                      border: '1px solid var(--bg-border)',
                      padding: '3px 8px',
                      borderRadius: 'var(--radius-sm)',
                      color:
                        copiedId === activeAttempt.id
                          ? 'var(--status-pass-text)'
                          : 'var(--text-muted)',
                      cursor: 'pointer',
                    }}
                  >
                    {copiedId === activeAttempt.id ? <Check size={12} /> : <Copy size={12} />}
                    <span>{copiedId === activeAttempt.id ? 'Copied Diff' : 'Copy Diff'}</span>
                  </button>
                </div>
              </div>

              {/* Diff Container */}
              {activeAttempt.diff_content ? (
                <div
                  style={{
                    padding: '12px',
                    background: '#070a10',
                    borderBottom: '1px solid var(--bg-border)',
                    overflowX: 'auto',
                  }}
                >
                  {activeAttempt.diff_content.split('\n').map((line, idx) =>
                    renderDiffLine(line, idx)
                  )}
                </div>
              ) : (
                <div style={{ padding: '20px', color: 'var(--text-muted)', fontSize: '13px' }}>
                  No diff content recorded.
                </div>
              )}

              {/* Isolated Verification Results */}
              {activeAttempt.validation_results && (
                <div style={{ padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
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
                    <div style={{ fontSize: '12px', color: 'var(--status-fail-text)' }}>
                      Failure reason: {activeAttempt.validation_results.failure_reason}
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
