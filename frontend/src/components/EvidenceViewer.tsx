import React, { useState } from 'react'
import {
  Copy,
  Check,
  ShieldAlert,
  AlertCircle,
  Hash,
  Terminal,
} from 'lucide-react'
import { EvidenceItem, EvidenceKind } from '../types'

interface EvidenceViewerProps {
  evidenceItems: EvidenceItem[]
  loading: boolean
  highlightedEvidenceId: string | null
  onClearHighlight?: () => void
}

export const EvidenceViewer: React.FC<EvidenceViewerProps> = ({
  evidenceItems,
  loading,
  highlightedEvidenceId,
  onClearHighlight,
}) => {
  const [filter, setFilter] = useState<string>('all')
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const copyContent = (id: string, text: string) => {
    navigator.clipboard.writeText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 1500)
  }

  const filteredItems = evidenceItems.filter((e) => {
    if (filter === 'all') return true
    return e.kind === filter
  })

  const getKindLabel = (kind: EvidenceKind) => {
    switch (kind) {
      case 'failed_assertion':
        return 'Failed Assertion'
      case 'stack_trace':
        return 'Stack Trace'
      case 'test_report':
        return 'Test Report'
      case 'log_excerpt':
        return 'Log Excerpt'
      case 'diff_excerpt':
        return 'Diff Excerpt'
      case 'route_result':
        return 'Route Result'
      default:
        return kind
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {/* Evidence Sub-header & Filter Bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '10px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Terminal size={15} color="var(--accent-cyan)" />
          <span style={{ fontSize: '13px', fontWeight: 600 }}>
            Redacted Factual Evidence ({evidenceItems.length} items)
          </span>
          {highlightedEvidenceId && (
            <button
              onClick={onClearHighlight}
              style={{
                fontSize: '11px',
                padding: '2px 8px',
                borderRadius: 'var(--radius-sm)',
                background: 'rgba(239, 68, 68, 0.15)',
                color: 'var(--status-fail-text)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
              }}
            >
              Clear citation filter
            </button>
          )}
        </div>

        {/* Kind Filters */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
          {['all', 'failed_assertion', 'stack_trace', 'log_excerpt'].map((k) => (
            <button
              key={k}
              onClick={() => setFilter(k)}
              style={{
                fontSize: '11px',
                padding: '3px 8px',
                borderRadius: 'var(--radius-sm)',
                border: 'none',
                background: filter === k ? 'var(--accent-cyan)' : 'var(--bg-surface-elevated)',
                color: filter === k ? '#090d16' : 'var(--text-muted)',
                fontWeight: filter === k ? 700 : 500,
                cursor: 'pointer',
              }}
            >
              {k === 'all' ? 'All Kinds' : getKindLabel(k as EvidenceKind)}
            </button>
          ))}
        </div>
      </div>

      {/* Items list */}
      {loading ? (
        <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
          Loading evidence items...
        </div>
      ) : filteredItems.length === 0 ? (
        <div
          style={{
            padding: '32px',
            textAlign: 'center',
            color: 'var(--text-muted)',
            background: 'var(--bg-surface)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--bg-border)',
          }}
        >
          No evidence items recorded for this filter.
        </div>
      ) : (
        filteredItems.map((item) => {
          const isHighlighted = item.id === highlightedEvidenceId

          return (
            <div
              key={item.id}
              id={`evidence-${item.id}`}
              style={{
                background: 'var(--bg-surface)',
                borderRadius: 'var(--radius-md)',
                border: isHighlighted
                  ? '2px solid var(--accent-cyan)'
                  : '1px solid var(--bg-border)',
                boxShadow: isHighlighted ? '0 0 15px rgba(6, 182, 212, 0.25)' : 'none',
                overflow: 'hidden',
                transition: 'var(--transition-fast)',
              }}
            >
              {/* Evidence Card Header */}
              <div
                style={{
                  padding: '10px 14px',
                  background: 'var(--bg-surface-elevated)',
                  borderBottom: '1px solid var(--bg-border)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span
                    style={{
                      fontSize: '11px',
                      fontWeight: 700,
                      padding: '2px 6px',
                      borderRadius: 'var(--radius-sm)',
                      background:
                        item.kind === 'failed_assertion'
                          ? 'rgba(239, 68, 68, 0.15)'
                          : 'rgba(59, 130, 246, 0.15)',
                      color:
                        item.kind === 'failed_assertion'
                          ? 'var(--status-fail-text)'
                          : '#93c5fd',
                      border:
                        item.kind === 'failed_assertion'
                          ? '1px solid rgba(239, 68, 68, 0.3)'
                          : '1px solid rgba(59, 130, 246, 0.3)',
                    }}
                  >
                    {getKindLabel(item.kind)}
                  </span>

                  <span
                    style={{
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)',
                      color: 'var(--text-main)',
                      fontWeight: 600,
                    }}
                  >
                    {item.source_label}
                  </span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  {/* Digest & Byte Length */}
                  <span
                    style={{
                      fontSize: '11px',
                      fontFamily: 'var(--font-mono)',
                      color: 'var(--text-subtle)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '3px',
                    }}
                  >
                    <Hash size={11} />
                    {item.content_digest.substring(0, 8)} &middot; {item.byte_length} B
                  </span>

                  {/* Copy Button */}
                  <button
                    onClick={() => copyContent(item.id, item.content_redacted)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px',
                      background: 'transparent',
                      border: '1px solid var(--bg-border)',
                      padding: '3px 8px',
                      borderRadius: 'var(--radius-sm)',
                      color: copiedId === item.id ? 'var(--status-pass-text)' : 'var(--text-muted)',
                      cursor: 'pointer',
                    }}
                  >
                    {copiedId === item.id ? <Check size={12} /> : <Copy size={12} />}
                    <span>{copiedId === item.id ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
              </div>

              {/* Evidence Code Box */}
              <div style={{ padding: '12px 14px', position: 'relative' }}>
                <pre
                  style={{
                    fontSize: '12px',
                    fontFamily: 'var(--font-mono)',
                    color: '#e2e8f0',
                    lineHeight: 1.5,
                    overflowX: 'auto',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {item.content_redacted}
                </pre>

                {item.truncated && (
                  <div
                    style={{
                      marginTop: '8px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      fontSize: '11px',
                      color: 'var(--status-timeout-text)',
                    }}
                  >
                    <AlertCircle size={12} />
                    <span>Output truncated to maintain 256 KiB size cap.</span>
                  </div>
                )}
              </div>

              {/* Footer: UUID and Redaction Guarantee */}
              <div
                style={{
                  padding: '6px 14px',
                  borderTop: '1px solid var(--bg-border)',
                  background: 'rgba(0, 0, 0, 0.2)',
                  fontSize: '11px',
                  color: 'var(--text-subtle)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  fontFamily: 'var(--font-mono)',
                }}
              >
                <span>ID: {item.id}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <ShieldAlert size={11} color="var(--accent-cyan)" />
                  Secrets Redacted
                </span>
              </div>
            </div>
          )
        })
      )}
    </div>
  )
}
