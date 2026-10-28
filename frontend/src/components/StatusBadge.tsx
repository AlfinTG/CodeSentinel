import React from 'react'
import {
  CheckCircle2,
  XCircle,
  Clock,
  Ban,
  AlertTriangle,
  Brain,
  Loader2,
  HelpCircle,
  Flame,
} from 'lucide-react'
import { TestOutcome, AnalysisOutcome, LifecycleStatus } from '../types'

interface StatusBadgeProps {
  type: 'test' | 'analysis' | 'lifecycle' | 'confidence'
  value: string
  size?: 'sm' | 'md'
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ type, value, size = 'sm' }) => {
  const isSm = size === 'sm'
  const padding = isSm ? '3px 8px' : '5px 12px'
  const fontSize = isSm ? '11px' : '13px'
  const iconSize = isSm ? 12 : 14

  if (type === 'test') {
    const outcome = value as TestOutcome
    switch (outcome) {
      case 'passed':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-pass-bg)',
              color: 'var(--status-pass-text)',
              border: '1px solid var(--status-pass-border)',
              letterSpacing: '0.02em',
            }}
          >
            <CheckCircle2 size={iconSize} />
            <span>Passed</span>
          </span>
        )
      case 'failed':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-fail-bg)',
              color: 'var(--status-fail-text)',
              border: '1px solid var(--status-fail-border)',
              letterSpacing: '0.02em',
            }}
          >
            <XCircle size={iconSize} />
            <span>Failed</span>
          </span>
        )
      case 'timed_out':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-timeout-bg)',
              color: 'var(--status-timeout-text)',
              border: '1px solid var(--status-timeout-border)',
            }}
          >
            <Clock size={iconSize} />
            <span>Timed Out</span>
          </span>
        )
      case 'cancelled':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-inconclusive-bg)',
              color: 'var(--status-inconclusive-text)',
              border: '1px solid var(--status-inconclusive-border)',
            }}
          >
            <Ban size={iconSize} />
            <span>Cancelled</span>
          </span>
        )
      case 'inconclusive':
      case 'error':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-inconclusive-bg)',
              color: '#e2e8f0',
              border: '1px solid var(--status-inconclusive-border)',
            }}
          >
            <AlertTriangle size={iconSize} />
            <span>{outcome === 'error' ? 'Error' : 'Inconclusive'}</span>
          </span>
        )
      case 'pending':
      default:
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'rgba(59, 130, 246, 0.15)',
              color: '#60a5fa',
              border: '1px solid rgba(59, 130, 246, 0.3)',
            }}
          >
            <Loader2 size={iconSize} className="animate-spin" />
            <span>Running</span>
          </span>
        )
    }
  }

  if (type === 'analysis') {
    const outcome = value as AnalysisOutcome
    switch (outcome) {
      case 'succeeded':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--analysis-ready-bg)',
              color: 'var(--analysis-ready-text)',
              border: '1px solid var(--analysis-ready-border)',
            }}
          >
            <Brain size={iconSize} />
            <span>Diagnosis Available</span>
          </span>
        )
      case 'running':
      case 'queued':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--analysis-running-bg)',
              color: 'var(--analysis-running-text)',
              border: '1px solid var(--analysis-running-border)',
            }}
          >
            <Loader2 size={iconSize} className="animate-spin" />
            <span>{outcome === 'queued' ? 'Queued' : 'Analyzing...'}</span>
          </span>
        )
      case 'unavailable':
      case 'invalid_response':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--analysis-error-bg)',
              color: 'var(--analysis-error-text)',
              border: '1px solid var(--analysis-error-border)',
            }}
          >
            <HelpCircle size={iconSize} />
            <span>{outcome === 'invalid_response' ? 'Invalid Response' : 'Unavailable'}</span>
          </span>
        )
      case 'not_requested':
      default:
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding,
              fontSize,
              fontWeight: 500,
              borderRadius: 'var(--radius-sm)',
              background: 'rgba(255, 255, 255, 0.05)',
              color: 'var(--text-subtle)',
              border: '1px solid var(--bg-border)',
            }}
          >
            <span>Not Requested</span>
          </span>
        )
    }
  }

  if (type === 'confidence') {
    const conf = value.toLowerCase()
    let bg = 'rgba(100, 116, 139, 0.2)'
    let text = '#94a3b8'
    let border = '#475569'

    if (conf === 'high') {
      bg = 'rgba(147, 51, 234, 0.2)'
      text = '#c084fc'
      border = 'rgba(147, 51, 234, 0.4)'
    } else if (conf === 'medium') {
      bg = 'rgba(59, 130, 246, 0.2)'
      text = '#93c5fd'
      border = 'rgba(59, 130, 246, 0.4)'
    }

    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 8px',
          fontSize: '11px',
          fontWeight: 600,
          borderRadius: 'var(--radius-sm)',
          background: bg,
          color: text,
          border: `1px solid ${border}`,
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
        }}
      >
        <Flame size={11} />
        <span>{conf} confidence</span>
      </span>
    )
  }

  // Lifecycle status fallback
  const lifecycle = value as LifecycleStatus
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding,
        fontSize,
        borderRadius: 'var(--radius-sm)',
        background: 'var(--bg-surface-elevated)',
        color: 'var(--text-muted)',
        border: '1px solid var(--bg-border)',
      }}
    >
      <span>{lifecycle}</span>
    </span>
  )
}

