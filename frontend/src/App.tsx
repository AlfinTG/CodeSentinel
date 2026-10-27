import { useEffect, useState } from 'react'
import { Shield, Terminal, Brain, CheckCircle2, Activity } from 'lucide-react'

interface HealthState {
  status: string
  database?: string
  queue?: string
}

export default function App() {
  const [health, setHealth] = useState<HealthState | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/v1/health/live')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data) => {
        setHealth(data)
        setLoading(false)
      })
      .catch((err) => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Top Navigation */}
      <header
        style={{
          borderBottom: '1px solid var(--bg-border)',
          background: 'var(--bg-surface)',
          padding: '16px 32px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div
            style={{
              width: '36px',
              height: '36px',
              borderRadius: 'var(--radius-md)',
              background: 'linear-gradient(135deg, #0284c7, #2563eb)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#ffffff',
            }}
          >
            <Shield size={20} />
          </div>
          <div>
            <h1 style={{ fontSize: '18px', fontWeight: 700, letterSpacing: '-0.02em' }}>
              CodeSentinel
            </h1>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              Evidence-Led Regression Diagnosis
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '13px',
              padding: '6px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--bg-border)',
            }}
          >
            <Activity size={14} color="var(--accent-cyan)" />
            <span>API Status:</span>
            {loading ? (
              <span style={{ color: 'var(--text-subtle)' }}>Connecting...</span>
            ) : error ? (
              <span style={{ color: 'var(--status-fail-text)' }}>Offline</span>
            ) : (
              <span style={{ color: 'var(--status-pass-text)', fontWeight: 600 }}>
                {health?.status === 'ok' ? 'Healthy' : 'Active'}
              </span>
            )}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main style={{ flex: 1, padding: '32px', maxWidth: '1200px', margin: '0 auto', width: '100%' }}>
        {/* Core Principles Banner */}
        <section
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--bg-border)',
            borderRadius: 'var(--radius-lg)',
            padding: '24px',
            marginBottom: '32px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px' }}>
            <div
              style={{
                padding: '10px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(6, 182, 212, 0.1)',
                border: '1px solid rgba(6, 182, 212, 0.2)',
                color: 'var(--accent-cyan)',
              }}
            >
              <CheckCircle2 size={24} />
            </div>
            <div>
              <h2 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '8px' }}>
                Strict Trust Boundary & Verification Invariant
              </h2>
              <p style={{ color: 'var(--text-muted)', fontSize: '14px', maxWidth: '800px', lineHeight: 1.6 }}>
                Observed test results are authoritative facts. AI model output is strictly a hypothesis grounded in bounded evidence. Model failure or latency never alters deterministic check outcomes.
              </p>
            </div>
          </div>
        </section>

        {/* Status Distinction Cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '24px' }}>
          {/* Card 1: Test Outcome */}
          <div
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-lg)',
              padding: '24px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px' }}>
              <Terminal size={18} color="var(--accent-cyan)" />
              <h3 style={{ fontSize: '16px', fontWeight: 600 }}>Deterministic Check Status</h3>
            </div>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              Isolated execution environment outcomes (P0 Scope).
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
              <span style={{ padding: '4px 10px', fontSize: '12px', fontWeight: 600, borderRadius: 'var(--radius-sm)', background: 'var(--status-pass-bg)', color: 'var(--status-pass-text)', border: '1px solid var(--status-pass-border)' }}>Passed</span>
              <span style={{ padding: '4px 10px', fontSize: '12px', fontWeight: 600, borderRadius: 'var(--radius-sm)', background: 'var(--status-fail-bg)', color: 'var(--status-fail-text)', border: '1px solid var(--status-fail-border)' }}>Failed</span>
              <span style={{ padding: '4px 10px', fontSize: '12px', fontWeight: 600, borderRadius: 'var(--radius-sm)', background: 'var(--status-timeout-bg)', color: 'var(--status-timeout-text)', border: '1px solid var(--status-timeout-border)' }}>Timed Out</span>
              <span style={{ padding: '4px 10px', fontSize: '12px', fontWeight: 600, borderRadius: 'var(--radius-sm)', background: 'var(--status-inconclusive-bg)', color: 'var(--status-inconclusive-text)', border: '1px solid var(--status-inconclusive-border)' }}>Inconclusive</span>
            </div>
          </div>

          {/* Card 2: AI Diagnosis */}
          <div
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-lg)',
              padding: '24px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px' }}>
              <Brain size={18} color="var(--accent-blue)" />
              <h3 style={{ fontSize: '16px', fontWeight: 600 }}>AI Diagnosis State</h3>
            </div>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              Nebius Token Factory served NVIDIA model hypotheses.
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
              <span style={{ padding: '4px 10px', fontSize: '12px', fontWeight: 600, borderRadius: 'var(--radius-sm)', background: 'var(--analysis-running-bg)', color: 'var(--analysis-running-text)', border: '1px solid var(--analysis-running-border)' }}>Running</span>
              <span style={{ padding: '4px 10px', fontSize: '12px', fontWeight: 600, borderRadius: 'var(--radius-sm)', background: 'var(--analysis-ready-bg)', color: 'var(--analysis-ready-text)', border: '1px solid var(--analysis-ready-border)' }}>Diagnosis Available</span>
              <span style={{ padding: '4px 10px', fontSize: '12px', fontWeight: 600, borderRadius: 'var(--radius-sm)', background: 'var(--analysis-error-bg)', color: 'var(--analysis-error-text)', border: '1px solid var(--analysis-error-border)' }}>Unavailable</span>
            </div>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer
        style={{
          borderTop: '1px solid var(--bg-border)',
          background: 'var(--bg-surface)',
          padding: '16px 32px',
          textAlign: 'center',
          fontSize: '12px',
          color: 'var(--text-subtle)',
        }}
      >
        CodeSentinel Phase 0 Baseline &middot; Apache 2.0 Open Source License &middot; Powered by Nebius Token Factory &amp; NVIDIA Open Models
      </footer>
    </div>
  )
}
