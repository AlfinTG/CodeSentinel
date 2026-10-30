import React, { useState, useMemo } from 'react'
import {
  Copy,
  Check,
  Columns,
  AlignLeft,
  WrapText,
  FileCode,
  PlusCircle,
  MinusCircle,
} from 'lucide-react'

interface DiffViewerProps {
  diffText: string
  title?: string
  initialMode?: 'unified' | 'split'
}

interface ParsedLine {
  type: 'add' | 'delete' | 'context' | 'header' | 'hunk'
  text: string
  oldLineNumber?: number
  newLineNumber?: number
}

interface ParsedFileDiff {
  oldPath: string
  newPath: string
  hunks: {
    header: string
    lines: ParsedLine[]
  }[]
  additions: number
  deletions: number
}

export const DiffViewer: React.FC<DiffViewerProps> = ({
  diffText,
  title,
  initialMode = 'unified',
}) => {
  const [mode, setMode] = useState<'unified' | 'split'>(initialMode)
  const [wrapLines, setWrapLines] = useState<boolean>(true)
  const [copied, setCopied] = useState<boolean>(false)

  // Parse unified diff text into structured format
  const parsed = useMemo<ParsedFileDiff>(() => {
    const lines = diffText.split('\n')
    let oldPath = 'file'
    let newPath = 'file'
    let additions = 0
    let deletions = 0

    const hunks: { header: string; lines: ParsedLine[] }[] = []
    let currentHunk: { header: string; lines: ParsedLine[] } | null = null

    let oldLine = 0
    let newLine = 0

    for (const rawLine of lines) {
      if (rawLine.startsWith('--- ')) {
        oldPath = rawLine.replace(/^---\s+(a\/)?/, '').trim()
        continue
      }
      if (rawLine.startsWith('+++ ')) {
        newPath = rawLine.replace(/^\+\+\+\s+(b\/)?/, '').trim()
        continue
      }
      if (rawLine.startsWith('diff --git')) {
        continue
      }
      if (rawLine.startsWith('index ') || rawLine.startsWith('similarity index')) {
        continue
      }

      if (rawLine.startsWith('@@')) {
        // Parse @@ -1,5 +1,6 @@
        const match = rawLine.match(/@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/)
        if (match) {
          oldLine = parseInt(match[1], 10)
          newLine = parseInt(match[2], 10)
        }
        currentHunk = { header: rawLine, lines: [] }
        hunks.push(currentHunk)
        continue
      }

      if (currentHunk) {
        if (rawLine.startsWith('+')) {
          additions++
          currentHunk.lines.push({
            type: 'add',
            text: rawLine.substring(1),
            newLineNumber: newLine++,
          })
        } else if (rawLine.startsWith('-')) {
          deletions++
          currentHunk.lines.push({
            type: 'delete',
            text: rawLine.substring(1),
            oldLineNumber: oldLine++,
          })
        } else {
          currentHunk.lines.push({
            type: 'context',
            text: rawLine.startsWith(' ') ? rawLine.substring(1) : rawLine,
            oldLineNumber: oldLine++,
            newLineNumber: newLine++,
          })
        }
      }
    }

    return {
      oldPath: oldPath || 'original',
      newPath: newPath || 'modified',
      hunks,
      additions,
      deletions,
    }
  }, [diffText])

  const copyToClipboard = async () => {
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(diffText)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }
    } catch (err) {
      console.warn('Failed to copy diff to clipboard:', err)
    }
  }

  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--bg-border)',
        borderRadius: 'var(--radius-lg)',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
      role="region"
      aria-label="Code Diff Viewer"
    >
      {/* Diff Toolbar */}
      <div
        style={{
          padding: '10px 16px',
          background: 'var(--bg-surface-elevated)',
          borderBottom: '1px solid var(--bg-border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <FileCode size={16} color="var(--accent-cyan)" />
          <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-main)', fontFamily: 'var(--font-mono)' }}>
            {title || parsed.newPath}
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', marginLeft: '6px' }}>
            <span style={{ color: 'var(--status-pass-text)', display: 'flex', alignItems: 'center', gap: '3px', fontWeight: 600 }}>
              <PlusCircle size={13} /> +{parsed.additions}
            </span>
            <span style={{ color: 'var(--status-fail-text)', display: 'flex', alignItems: 'center', gap: '3px', fontWeight: 600 }}>
              <MinusCircle size={13} /> -{parsed.deletions}
            </span>
          </div>
        </div>

        {/* View Mode & Actions */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {/* Mode Switcher */}
          <div
            style={{
              display: 'flex',
              background: 'var(--bg-primary)',
              borderRadius: 'var(--radius-sm)',
              padding: '2px',
              border: '1px solid var(--bg-border)',
            }}
            role="group"
            aria-label="Diff view options"
          >
            <button
              onClick={() => setMode('unified')}
              style={{
                background: mode === 'unified' ? 'var(--bg-surface-elevated)' : 'transparent',
                color: mode === 'unified' ? 'var(--text-main)' : 'var(--text-subtle)',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                padding: '4px 10px',
                fontSize: '12px',
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                cursor: 'pointer',
              }}
              aria-pressed={mode === 'unified'}
              title="Unified diff view"
            >
              <AlignLeft size={13} /> Unified
            </button>
            <button
              onClick={() => setMode('split')}
              style={{
                background: mode === 'split' ? 'var(--bg-surface-elevated)' : 'transparent',
                color: mode === 'split' ? 'var(--text-main)' : 'var(--text-subtle)',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                padding: '4px 10px',
                fontSize: '12px',
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                cursor: 'pointer',
              }}
              aria-pressed={mode === 'split'}
              title="Side-by-side split view"
            >
              <Columns size={13} /> Split
            </button>
          </div>

          {/* Wrap lines toggle */}
          <button
            onClick={() => setWrapLines(!wrapLines)}
            style={{
              background: wrapLines ? 'var(--bg-surface-elevated)' : 'transparent',
              color: wrapLines ? 'var(--accent-cyan)' : 'var(--text-subtle)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-sm)',
              padding: '4px 10px',
              fontSize: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '5px',
              cursor: 'pointer',
            }}
            aria-pressed={wrapLines}
            title="Toggle word wrap"
          >
            <WrapText size={13} /> Wrap
          </button>

          {/* Copy diff button */}
          <button
            onClick={copyToClipboard}
            style={{
              background: 'transparent',
              color: copied ? 'var(--status-pass-text)' : 'var(--text-muted)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-sm)',
              padding: '4px 10px',
              fontSize: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '5px',
              cursor: 'pointer',
            }}
            title="Copy diff to clipboard"
            aria-label="Copy diff"
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>

      {/* Diff Content Body */}
      <div
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '12.5px',
          lineHeight: 1.5,
          overflowX: 'auto',
          maxHeight: '520px',
        }}
        tabIndex={0}
        aria-label="Diff contents scrollable container"
      >
        {parsed.hunks.length === 0 ? (
          <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)' }}>
            No textual diff changes available for display.
          </div>
        ) : (
          parsed.hunks.map((hunk, hunkIdx) => (
            <div key={hunkIdx} style={{ borderBottom: '1px solid var(--bg-border)' }}>
              {/* Hunk Header */}
              <div
                style={{
                  background: 'rgba(6, 182, 212, 0.08)',
                  color: 'var(--accent-cyan)',
                  padding: '4px 14px',
                  fontSize: '11px',
                  borderTop: hunkIdx > 0 ? '1px solid var(--bg-border)' : 'none',
                  borderBottom: '1px solid rgba(6, 182, 212, 0.15)',
                  userSelect: 'none',
                }}
              >
                {hunk.header}
              </div>

              {/* Render unified or split */}
              {mode === 'unified' ? (
                <div>
                  {hunk.lines.map((line, lineIdx) => {
                    const isAdd = line.type === 'add'
                    const isDel = line.type === 'delete'

                    let bg = 'transparent'
                    let textColor = 'var(--text-main)'
                    let sign = ' '

                    if (isAdd) {
                      bg = 'rgba(16, 185, 129, 0.14)'
                      textColor = '#34d399'
                      sign = '+'
                    } else if (isDel) {
                      bg = 'rgba(239, 68, 68, 0.14)'
                      textColor = '#f87171'
                      sign = '-'
                    }

                    return (
                      <div
                        key={lineIdx}
                        style={{
                          display: 'flex',
                          background: bg,
                          borderLeft: isAdd
                            ? '3px solid var(--status-pass-border)'
                            : isDel
                            ? '3px solid var(--status-fail-border)'
                            : '3px solid transparent',
                        }}
                      >
                        {/* Old line number */}
                        <div
                          style={{
                            width: '44px',
                            minWidth: '44px',
                            padding: '1px 8px',
                            textAlign: 'right',
                            color: 'var(--text-subtle)',
                            fontSize: '11px',
                            userSelect: 'none',
                            borderRight: '1px solid var(--bg-border)',
                          }}
                        >
                          {line.oldLineNumber || ''}
                        </div>

                        {/* New line number */}
                        <div
                          style={{
                            width: '44px',
                            minWidth: '44px',
                            padding: '1px 8px',
                            textAlign: 'right',
                            color: 'var(--text-subtle)',
                            fontSize: '11px',
                            userSelect: 'none',
                            borderRight: '1px solid var(--bg-border)',
                          }}
                        >
                          {line.newLineNumber || ''}
                        </div>

                        {/* Sign */}
                        <div
                          style={{
                            width: '20px',
                            minWidth: '20px',
                            textAlign: 'center',
                            userSelect: 'none',
                            color: textColor,
                            fontWeight: 600,
                          }}
                        >
                          {sign}
                        </div>

                        {/* Line content */}
                        <div
                          style={{
                            flex: 1,
                            padding: '1px 8px',
                            color: textColor,
                            whiteSpace: wrapLines ? 'pre-wrap' : 'pre',
                            wordBreak: wrapLines ? 'break-all' : 'normal',
                          }}
                        >
                          {line.text}
                        </div>
                      </div>
                    )
                  })}
                </div>
              ) : (
                /* Split Mode */
                <div>
                  {hunk.lines.map((line, lineIdx) => {
                    const isAdd = line.type === 'add'
                    const isDel = line.type === 'delete'

                    return (
                      <div
                        key={lineIdx}
                        style={{
                          display: 'flex',
                          borderBottom: '1px solid rgba(255, 255, 255, 0.02)',
                        }}
                      >
                        {/* Left Column (Old/Deletions) */}
                        <div
                          style={{
                            flex: 1,
                            display: 'flex',
                            background: isDel ? 'rgba(239, 68, 68, 0.14)' : 'transparent',
                            borderRight: '1px solid var(--bg-border)',
                          }}
                        >
                          <div
                            style={{
                              width: '40px',
                              minWidth: '40px',
                              padding: '1px 6px',
                              textAlign: 'right',
                              color: 'var(--text-subtle)',
                              fontSize: '11px',
                              userSelect: 'none',
                              borderRight: '1px solid var(--bg-border)',
                            }}
                          >
                            {line.oldLineNumber || ''}
                          </div>
                          <div
                            style={{
                              flex: 1,
                              padding: '1px 8px',
                              color: isDel ? '#f87171' : 'var(--text-muted)',
                              whiteSpace: wrapLines ? 'pre-wrap' : 'pre',
                              wordBreak: wrapLines ? 'break-all' : 'normal',
                            }}
                          >
                            {isDel || line.type === 'context' ? line.text : ''}
                          </div>
                        </div>

                        {/* Right Column (New/Additions) */}
                        <div
                          style={{
                            flex: 1,
                            display: 'flex',
                            background: isAdd ? 'rgba(16, 185, 129, 0.14)' : 'transparent',
                          }}
                        >
                          <div
                            style={{
                              width: '40px',
                              minWidth: '40px',
                              padding: '1px 6px',
                              textAlign: 'right',
                              color: 'var(--text-subtle)',
                              fontSize: '11px',
                              userSelect: 'none',
                              borderRight: '1px solid var(--bg-border)',
                            }}
                          >
                            {line.newLineNumber || ''}
                          </div>
                          <div
                            style={{
                              flex: 1,
                              padding: '1px 8px',
                              color: isAdd ? '#34d399' : 'var(--text-muted)',
                              whiteSpace: wrapLines ? 'pre-wrap' : 'pre',
                              wordBreak: wrapLines ? 'break-all' : 'normal',
                            }}
                          >
                            {isAdd || line.type === 'context' ? line.text : ''}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

