import { useEffect, useState, useCallback } from 'react'
import {
  AlertCircle,
  FolderGit2,
  CheckCircle2,
  Info,
} from 'lucide-react'
import {
  HealthState,
  Project,
  ProjectDetail,
  Run,
  RunDetail,
  CheckRun,
  EvidenceItem,
  Analysis,
  PatchAttempt,
} from './types'
import { api } from './services/api'
import { Navbar } from './components/Navbar'
import { RunList } from './components/RunList'
import { RunDetailView } from './components/RunDetailView'

export default function App() {
  // Global & System state
  const [health, setHealth] = useState<HealthState | null>(null)
  const [healthLoading, setHealthLoading] = useState<boolean>(true)
  const [healthError, setHealthError] = useState<string | null>(null)

  // Projects state
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [projectDetail, setProjectDetail] = useState<ProjectDetail | null>(null)
  const [seeding, setSeeding] = useState<boolean>(false)

  // Runs state
  const [runs, setRuns] = useState<Run[]>([])
  const [runsLoading, setRunsLoading] = useState<boolean>(false)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  // Active Run Detail state
  const [activeRun, setActiveRun] = useState<RunDetail | null>(null)
  const [activeChecks, setActiveChecks] = useState<CheckRun[]>([])
  const [activeEvidence, setActiveEvidence] = useState<EvidenceItem[]>([])
  const [activeAnalysis, setActiveAnalysis] = useState<Analysis | null>(null)
  const [activePatchAttempts, setActivePatchAttempts] = useState<PatchAttempt[]>([])
  const [detailLoading, setDetailLoading] = useState<boolean>(false)

  // Actions in flight
  const [triggeringAnalysis, setTriggeringAnalysis] = useState<boolean>(false)
  const [triggeringPatch, setTriggeringPatch] = useState<boolean>(false)
  const [cancellingRun, setCancellingRun] = useState<boolean>(false)
  const [bannerNotice, setBannerNotice] = useState<{ message: string; type: 'info' | 'error' } | null>(null)

  // 1. Initial health & projects load
  useEffect(() => {
    api
      .getHealthLive()
      .then((data) => {
        setHealth(data)
        setHealthLoading(false)
      })
      .catch((err) => {
        setHealthError(err.message)
        setHealthLoading(false)
      })

    loadProjects()
  }, [])

  const loadProjects = async () => {
    try {
      const items = await api.getProjects()
      setProjects(items)
      if (items.length > 0 && !selectedProjectId) {
        setSelectedProjectId(items[0].id)
      }
    } catch (err: any) {
      console.error('Failed to load projects:', err)
    }
  }

  // 2. Load runs when selectedProjectId changes
  const loadRuns = useCallback(async (projectId: string) => {
    setRunsLoading(true)
    try {
      const res = await api.getProjectRuns(projectId)
      setRuns(res.items)
      // Auto-select first run if none selected or previous not in list
      if (res.items.length > 0) {
        setSelectedRunId((prev) => (res.items.some((r) => r.id === prev) ? prev : res.items[0].id))
      } else {
        setSelectedRunId(null)
      }
    } catch (err: any) {
      setBannerNotice({ message: `Failed to load runs: ${err.message}`, type: 'error' })
    } finally {
      setRunsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selectedProjectId) {
      api.getProject(selectedProjectId).then(setProjectDetail).catch(console.error)
      loadRuns(selectedProjectId)
    }
  }, [selectedProjectId, loadRuns])

  // 3. Load active run detail when selectedRunId changes
  const loadRunDetail = useCallback(async (runId: string) => {
    setDetailLoading(true)
    try {
      const [runData, checksData, evidenceData, patchesData] = await Promise.all([
        api.getRun(runId),
        api.getRunChecks(runId),
        api.getRunEvidence(runId),
        api.getRunPatchAttempts(runId).catch(() => []),
      ])
      setActiveRun(runData)
      setActiveChecks(checksData)
      setActiveEvidence(evidenceData)
      setActivePatchAttempts(patchesData)

      // Fetch analysis separately (404 is normal if not requested)
      try {
        const analysisData = await api.getRunAnalysis(runId)
        setActiveAnalysis(analysisData)
      } catch {
        setActiveAnalysis(null)
      }
    } catch (err: any) {
      setBannerNotice({ message: `Error loading run detail: ${err.message}`, type: 'error' })
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selectedRunId) {
      loadRunDetail(selectedRunId)
    } else {
      setActiveRun(null)
      setActiveChecks([])
      setActiveEvidence([])
      setActiveAnalysis(null)
      setActivePatchAttempts([])
    }
  }, [selectedRunId, loadRunDetail])

  // 4. Seed demo handler
  const handleSeedDemo = async () => {
    setSeeding(true)
    try {
      const demo = await api.seedDemoProject()
      await loadProjects()
      setSelectedProjectId(demo.id)
      setBannerNotice({
        message: 'Demo project loaded: baseline passing run, regression with AI diagnosis, and in-flight runs ready.',
        type: 'info',
      })
    } catch (err: any) {
      setBannerNotice({ message: `Seeding error: ${err.message}`, type: 'error' })
    } finally {
      setSeeding(false)
    }
  }

  // 5. Trigger Analysis handler
  const handleRequestAnalysis = async () => {
    if (!activeRun) return
    setTriggeringAnalysis(true)
    try {
      const res = await api.requestAnalysis(activeRun.id)
      setActiveAnalysis(res)
      setBannerNotice({
        message: 'AI diagnosis requested from Nebius Token Factory NVIDIA model. Running in background...',
        type: 'info',
      })
      // Refresh run to update analysis_outcome
      setTimeout(() => {
        if (selectedProjectId) loadRuns(selectedProjectId)
        loadRunDetail(activeRun.id)
      }, 1200)
    } catch (err: any) {
      setBannerNotice({ message: `Analysis request error: ${err.message}`, type: 'error' })
    } finally {
      setTriggeringAnalysis(false)
    }
  }

  // 6. Retry Analysis handler
  const handleRetryAnalysis = async () => {
    if (!activeRun) return
    setTriggeringAnalysis(true)
    try {
      const res = await api.retryAnalysis(activeRun.id)
      setActiveAnalysis(res)
      setBannerNotice({
        message: 'Analysis retry queued. Calling Nebius Token Factory...',
        type: 'info',
      })
      setTimeout(() => {
        if (selectedProjectId) loadRuns(selectedProjectId)
        loadRunDetail(activeRun.id)
      }, 1200)
    } catch (err: any) {
      setBannerNotice({ message: `Retry error: ${err.message}`, type: 'error' })
    } finally {
      setTriggeringAnalysis(false)
    }
  }

  // 7. Cancel Run handler
  const handleCancelRun = async () => {
    if (!activeRun) return
    setCancellingRun(true)
    try {
      const updated = await api.cancelRun(activeRun.id)
      setActiveRun((prev) => (prev ? { ...prev, ...updated } : null))
      setBannerNotice({ message: `Run ${activeRun.id.substring(0, 8)} cancelled.`, type: 'info' })
      if (selectedProjectId) loadRuns(selectedProjectId)
    } catch (err: any) {
      setBannerNotice({ message: `Cancel error: ${err.message}`, type: 'error' })
    } finally {
      setCancellingRun(false)
    }
  }

  // 8. Propose & Validate Candidate Patch handler (Phase 6 / P1)
  const handleProposePatch = async (customInstructions?: string) => {
    if (!activeRun) return
    setTriggeringPatch(true)
    try {
      const newAttempt = await api.createPatchAttempt(activeRun.id, customInstructions)
      setActivePatchAttempts((prev) => [newAttempt, ...prev])
      const isVerified = newAttempt.status === 'verified'
      setBannerNotice({
        message: isVerified
          ? `Candidate patch verified! All checks passed exit 0 in isolated sandbox.`
          : `Candidate patch tested in isolated sandbox. Status: ${newAttempt.status}.`,
        type: isVerified ? 'info' : 'error',
      })
    } catch (err: any) {
      setBannerNotice({ message: `Patch error: ${err.message}`, type: 'error' })
    } finally {
      setTriggeringPatch(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Top Navigation */}
      <Navbar
        health={health}
        healthLoading={healthLoading}
        healthError={healthError}
        projects={projects}
        selectedProjectId={selectedProjectId}
        onSelectProject={(id) => setSelectedProjectId(id)}
        onSeedDemo={handleSeedDemo}
        seeding={seeding}
      />

      {/* Main Container */}
      <main style={{ flex: 1, padding: '24px 32px', maxWidth: '1440px', margin: '0 auto', width: '100%' }}>
        {/* Banner Notice (Errors / Confirmations) */}
        {bannerNotice && (
          <div
            style={{
              marginBottom: '20px',
              padding: '12px 18px',
              borderRadius: 'var(--radius-md)',
              background:
                bannerNotice.type === 'error'
                  ? 'rgba(239, 68, 68, 0.15)'
                  : 'rgba(6, 182, 212, 0.15)',
              border: `1px solid ${
                bannerNotice.type === 'error'
                  ? 'rgba(239, 68, 68, 0.3)'
                  : 'rgba(6, 182, 212, 0.3)'
              }`,
              color:
                bannerNotice.type === 'error'
                  ? 'var(--status-fail-text)'
                  : 'var(--accent-cyan)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: '13px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              {bannerNotice.type === 'error' ? <AlertCircle size={16} /> : <Info size={16} />}
              <span>{bannerNotice.message}</span>
            </div>
            <button
              onClick={() => setBannerNotice(null)}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'inherit',
                cursor: 'pointer',
                fontWeight: 600,
              }}
            >
              &times;
            </button>
          </div>
        )}

        {/* Core Principles & Trust Boundary Banner */}
        <section
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--bg-border)',
            borderRadius: 'var(--radius-lg)',
            padding: '18px 24px',
            marginBottom: '24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '16px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
            <div
              style={{
                width: '38px',
                height: '38px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(6, 182, 212, 0.1)',
                border: '1px solid rgba(6, 182, 212, 0.25)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--accent-cyan)',
              }}
            >
              <CheckCircle2 size={20} />
            </div>
            <div>
              <h2 style={{ fontSize: '15px', fontWeight: 700, letterSpacing: '-0.01em' }}>
                Strict Verification Invariant
              </h2>
              <p style={{ color: 'var(--text-muted)', fontSize: '13px', maxWidth: '780px' }}>
                Observed test results are authoritative facts. AI model output is strictly a hypothesis grounded in bounded evidence. Model failure or latency never alters deterministic check outcomes.
              </p>
            </div>
          </div>

          {projectDetail && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '12px',
                background: 'var(--bg-surface-elevated)',
                padding: '6px 14px',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--bg-border)',
                fontFamily: 'var(--font-mono)',
              }}
            >
              <FolderGit2 size={14} color="var(--accent-cyan)" />
              <span style={{ color: 'var(--text-main)', fontWeight: 600 }}>{projectDetail.repository}</span>
              <span style={{ color: 'var(--text-subtle)' }}>({projectDetail.default_branch})</span>
            </div>
          )}
        </section>

        {/* Dashboard Body: Master-Detail Grid */}
        {projects.length === 0 ? (
          <div
            style={{
              padding: '64px 20px',
              textAlign: 'center',
              background: 'var(--bg-surface)',
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--bg-border)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '16px',
            }}
          >
            <FolderGit2 size={36} color="var(--accent-cyan)" />
            <div>
              <h3 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '6px' }}>
                No Projects Connected
              </h3>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', maxWidth: '440px' }}>
                Connect a GitHub repository via webhook or load our pre-configured demo project to explore the Phase 5 dashboard.
              </p>
            </div>
            <button
              onClick={handleSeedDemo}
              disabled={seeding}
              style={{
                background: 'var(--accent-blue)',
                color: '#ffffff',
                border: 'none',
                padding: '10px 20px',
                borderRadius: 'var(--radius-sm)',
                fontSize: '13px',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {seeding ? 'Loading Demo...' : 'Load Acme Corp Demo Project'}
            </button>
          </div>
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(320px, 420px) minmax(500px, 1fr)',
              gap: '24px',
              alignItems: 'start',
            }}
          >
            {/* Left Column: Run History */}
            <RunList
              runs={runs}
              selectedRunId={selectedRunId}
              onSelectRun={(id) => setSelectedRunId(id)}
              loading={runsLoading}
            />

            {/* Right Column: Run Detail with Checks, Evidence & AI Diagnosis */}
            {activeRun ? (
              <RunDetailView
                run={activeRun}
                checks={activeChecks}
                evidence={activeEvidence}
                analysis={activeAnalysis}
                patchAttempts={activePatchAttempts}
                loadingDetail={detailLoading}
                triggeringAnalysis={triggeringAnalysis}
                triggeringPatch={triggeringPatch}
                cancellingRun={cancellingRun}
                onRequestAnalysis={handleRequestAnalysis}
                onRetryAnalysis={handleRetryAnalysis}
                onProposePatch={handleProposePatch}
                onCancelRun={handleCancelRun}
              />
            ) : (
              <div
                style={{
                  background: 'var(--bg-surface)',
                  borderRadius: 'var(--radius-lg)',
                  border: '1px solid var(--bg-border)',
                  padding: '48px 24px',
                  textAlign: 'center',
                  color: 'var(--text-muted)',
                }}
              >
                Select a run from the history to view deterministic checks, bounded evidence, and structured AI diagnosis.
              </div>
            )}
          </div>
        )}
      </main>

      {/* Footer */}
      <footer
        style={{
          borderTop: '1px solid var(--bg-border)',
          background: 'var(--bg-surface)',
          padding: '14px 32px',
          textAlign: 'center',
          fontSize: '12px',
          color: 'var(--text-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span>CodeSentinel Phase 5 &middot; Evidence-Led Regression Diagnosis</span>
        <span>Apache 2.0 Open Source &middot; Powered by Nebius Token Factory &amp; NVIDIA Open Models</span>
      </footer>
    </div>
  )
}
