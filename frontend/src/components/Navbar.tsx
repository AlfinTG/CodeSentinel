import React from 'react'
import { Shield, Activity, GitBranch, Database, RefreshCw, FolderGit2 } from 'lucide-react'
import { HealthState, Project } from '../types'

interface NavbarProps {
  health: HealthState | null
  healthLoading: boolean
  healthError: string | null
  projects: Project[]
  selectedProjectId: string | null
  onSelectProject: (id: string) => void
  onSeedDemo: () => void
  seeding: boolean
}

export const Navbar: React.FC<NavbarProps> = ({
  health,
  healthLoading,
  healthError,
  projects,
  selectedProjectId,
  onSelectProject,
  onSeedDemo,
  seeding,
}) => {
  return (
    <header
      style={{
        borderBottom: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
        padding: '12px 28px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        position: 'sticky',
        top: 0,
        zIndex: 50,
      }}
    >
      {/* Brand */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div
            style={{
              width: '34px',
              height: '34px',
              borderRadius: 'var(--radius-md)',
              background: 'linear-gradient(135deg, #0284c7, #2563eb)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#ffffff',
              boxShadow: '0 2px 8px rgba(37, 99, 235, 0.3)',
            }}
          >
            <Shield size={18} />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '16px', fontWeight: 700, letterSpacing: '-0.02em' }}>
                CodeSentinel
              </span>
              <span
                style={{
                  fontSize: '10px',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  padding: '1px 6px',
                  borderRadius: '3px',
                  background: 'rgba(6, 182, 212, 0.15)',
                  color: 'var(--accent-cyan)',
                  border: '1px solid rgba(6, 182, 212, 0.3)',
                }}
              >
                Phase 5 MVP
              </span>
            </div>
            <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              Evidence-Led Regression Diagnosis
            </p>
          </div>
        </div>

        {/* Project Selector */}
        {projects.length > 0 && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              background: 'var(--bg-surface-elevated)',
              padding: '4px 10px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--bg-border)',
            }}
          >
            <FolderGit2 size={15} color="var(--accent-cyan)" />
            <select
              value={selectedProjectId || ''}
              onChange={(e) => onSelectProject(e.target.value)}
              style={{
                background: 'transparent',
                color: 'var(--text-main)',
                border: 'none',
                outline: 'none',
                fontSize: '13px',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {projects.map((p) => (
                <option
                  key={p.id}
                  value={p.id}
                  style={{ background: 'var(--bg-surface)', color: 'var(--text-main)' }}
                >
                  {p.repository}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Right Controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
        {/* Seed Demo Button */}
        <button
          onClick={onSeedDemo}
          disabled={seeding}
          title="Seed realistic baseline and regression fixtures for demonstration"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            background: 'var(--bg-surface-elevated)',
            border: '1px solid var(--bg-border-light)',
            color: 'var(--text-main)',
            fontSize: '12px',
            padding: '6px 12px',
            borderRadius: 'var(--radius-sm)',
            transition: 'var(--transition-fast)',
            opacity: seeding ? 0.7 : 1,
          }}
        >
          {seeding ? (
            <RefreshCw size={13} className="animate-spin" />
          ) : (
            <Database size={13} color="var(--accent-cyan)" />
          )}
          <span>{seeding ? 'Seeding...' : 'Load Demo Project'}</span>
        </button>

        {/* API Health Pill */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '12px',
            padding: '5px 10px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-surface-elevated)',
            border: '1px solid var(--bg-border)',
          }}
        >
          <Activity
            size={13}
            color={
              healthError
                ? 'var(--status-fail-text)'
                : health?.status === 'ok'
                ? 'var(--status-pass-text)'
                : 'var(--text-subtle)'
            }
          />
          <span style={{ color: 'var(--text-muted)' }}>API:</span>
          {healthLoading ? (
            <span style={{ color: 'var(--text-subtle)' }}>Checking...</span>
          ) : healthError ? (
            <span style={{ color: 'var(--status-fail-text)', fontWeight: 600 }}>Offline</span>
          ) : (
            <span style={{ color: 'var(--status-pass-text)', fontWeight: 600 }}>Ready</span>
          )}
        </div>

        {/* Developer user */}
        <div
          style={{
            fontSize: '12px',
            color: 'var(--text-muted)',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            paddingLeft: '6px',
            borderLeft: '1px solid var(--bg-border)',
          }}
        >
          <GitBranch size={13} />
          <span>demo-dev</span>
        </div>
      </div>
    </header>
  )
}

