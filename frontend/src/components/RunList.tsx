import React, { useState } from 'react'
import { GitCommit, GitPullRequest, Copy, Check, Filter } from 'lucide-react'
import { Run } from '../types'
import { StatusBadge } from './StatusBadge'

interface RunListProps {
  runs: Run[]
  selectedRunId: string | null
  onSelectRun: (runId: string) => void
  loading: boolean
}

export const RunList: React.FC<RunListProps> = ({
  runs,
  selectedRunId,
  onSelectRun,
  loading,
}) => {
  const [filter, setFilter] = useState<'all' | 'failed' | 'passed' | 'running'>('all')
  const [copiedSha, setCopiedSha] = useState<string | null>(null)

  const copySha = (sha: string, e: React.MouseEvent) => {
    e.stopPropagation()
    navigator.clipboard.writeText(sha)
    setCopiedSha(sha)
    setTimeout(() => setCopiedSha(null), 1500)
  }

  const filteredRuns = runs.filter((r) => {
    if (filter === 'all') return true
    if (filter === 'failed') return r.test_outcome === 'failed'
    if (filter === 'passed') return r.test_outcome === 'passed'
    if (filter === 'running') return r.lifecycle_status === 'running'
    return true
  })

  const formatDuration = (run: Run) => {
    if (!run.started_at || !run.completed_at) return null
    const diffMs =
      new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()
    if (diffMs <= 0) return null
    return `${(diffMs / 1000).toFixed(1)}s`
  }

  const formatRelativeTime = (isoString: string) => {
    try {
      const date = new Date(isoString)
      const now = new Date()
      const diffSec = Math.floor((now.getTime() - date.getTime()) / 1000)
      if (diffSec < 60) return `${diffSec}s ago`
      if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
      if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
      return `${Math.floor(diffSec / 86400)}d ago`
    } catch {
      return isoString
    }
  }

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
      {/* Header & Filter Controls */}
      <div
        style={{
          padding: '14px 18px',
          borderBottom: '1px solid var(--bg-border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'var(--bg-surface-elevated)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '14px', fontWeight: 700 }}>Runs & Executions</span>
          <span
            style={{
              fontSize: '11px',
              padding: '1px 6px',
              borderRadius: '10px',
              background: 'rgba(255, 255, 255, 0.08)',
              color: 'var(--text-muted)',
            }}
          >
            {runs.length}
          </span>
        </div>

        {/* Filter Pills */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Filter size={12} color="var(--text-subtle)" />
          {(['all', 'failed', 'passed', 'running'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                fontSize: '11px',
                padding: '3px 8px',
                borderRadius: 'var(--radius-sm)',
                border: 'none',
                background: filter === f ? 'var(--accent-blue)' : 'transparent',
                color: filter === f ? '#ffffff' : 'var(--text-muted)',
                fontWeight: filter === f ? 600 : 400,
                cursor: 'pointer',
                transition: 'var(--transition-fast)',
              }}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Runs List Items */}
      <div style={{ maxHeight: '600px', overflowY: 'auto' }}>
        {loading ? (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
            Loading run history...
          </div>
        ) : filteredRuns.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
            No runs found matching filter "{filter}".
          </div>
        ) : (
          filteredRuns.map((run) => {
            const isSelected = run.id === selectedRunId
            const duration = formatDuration(run)

            return (
              <div
                key={run.id}
                onClick={() => onSelectRun(run.id)}
                style={{
                  padding: '14px 18px',
                  borderBottom: '1px solid var(--bg-border)',
                  cursor: 'pointer',
                  background: isSelected ? 'rgba(6, 182, 212, 0.08)' : 'transparent',
                  borderLeft: isSelected
                    ? '3px solid var(--accent-cyan)'
                    : '3px solid transparent',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  transition: 'var(--transition-fast)',
                }}
              >
                {/* Left: Commit info & event */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        fontSize: '12px',
                        fontFamily: 'var(--font-mono)',
                        color: 'var(--text-main)',
                        fontWeight: 600,
                      }}
                    >
                      <GitCommit size={14} color="var(--accent-cyan)" />
                      <span>{run.head_sha.substring(0, 7)}</span>
                      <button
                        onClick={(e) => copySha(run.head_sha, e)}
                        title="Copy full SHA"
                        style={{
                          background: 'transparent',
                          border: 'none',
                          padding: '2px',
                          display: 'inline-flex',
                          alignItems: 'center',
                          color: copiedSha === run.head_sha ? 'var(--status-pass-text)' : 'var(--text-subtle)',
                        }}
                      >
                        {copiedSha === run.head_sha ? <Check size={12} /> : <Copy size={12} />}
                      </button>
                    </div>

                    {/* Event badge */}
                    <span
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '4px',
                        fontSize: '11px',
                        color: 'var(--text-muted)',
                        background: 'rgba(255, 255, 255, 0.05)',
                        padding: '2px 6px',
                        borderRadius: 'var(--radius-sm)',
                      }}
                    >
                      {run.event_kind === 'pull_request' ? (
                        <>
                          <GitPullRequest size={11} color="var(--accent-blue)" />
                          <span>PR #{run.pr_number || '42'}</span>
                        </>
                      ) : (
                        <>
                          <GitCommit size={11} />
                          <span>push</span>
                        </>
                      )}
                    </span>

                    <span style={{ fontSize: '11px', color: 'var(--text-subtle)' }}>
                      {formatRelativeTime(run.created_at)}
                    </span>
                  </div>

                  {/* Summary / Duration */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '11px', color: 'var(--text-subtle)' }}>
                    <span>Profile v{run.check_profile_version}</span>
                    {duration && <span>Duration: {duration}</span>}
                    {run.failure_code && (
                      <span style={{ color: 'var(--status-fail-text)', fontFamily: 'var(--font-mono)' }}>
                        [{run.failure_code}]
                      </span>
                    )}
                  </div>
                </div>

                {/* Right: Badges */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <StatusBadge type="test" value={run.test_outcome} />
                  <StatusBadge type="analysis" value={run.analysis_outcome} />
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
