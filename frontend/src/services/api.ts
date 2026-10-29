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
  CurrentUserResponse,
  ApiError,
} from '../types'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }

  const response = await fetch(path, { ...options, headers, credentials: 'include' })

  if (!response.ok) {
    let errDetail: ApiError = {
      code: `http_${response.status}`,
      message: `HTTP ${response.status}: ${response.statusText}`,
    }
    try {
      const data = await response.json()
      if (data.error) {
        errDetail = data.error
      }
    } catch {
      // fallback to statusText
    }
    const error = new Error(errDetail.message) as Error & { apiError: ApiError }
    error.apiError = errDetail
    throw error
  }

  return response.json() as Promise<T>
}

export const api = {
  async getHealthLive(): Promise<HealthState> {
    return request<HealthState>('/api/v1/health/live')
  },

  async getHealthReady(): Promise<HealthState> {
    return request<HealthState>('/api/v1/health/ready')
  },

  async getProjects(): Promise<Project[]> {
    const res = await request<{ items: Project[] }>('/api/v1/projects')
    return res.items
  },

  async getProject(projectId: string): Promise<ProjectDetail> {
    return request<ProjectDetail>(`/api/v1/projects/${projectId}`)
  },

  async seedDemoProject(): Promise<ProjectDetail> {
    return request<ProjectDetail>('/api/v1/projects/seed-demo', {
      method: 'POST',
    })
  },

  async getProjectRuns(projectId: string, limit = 50, cursor?: string): Promise<{ items: Run[]; next_cursor?: string | null }> {
    const params = new URLSearchParams({ limit: limit.toString() })
    if (cursor) params.append('cursor', cursor)
    return request<{ items: Run[]; next_cursor?: string | null }>(
      `/api/v1/projects/${projectId}/runs?${params.toString()}`
    )
  },

  async getRun(runId: string): Promise<RunDetail> {
    return request<RunDetail>(`/api/v1/runs/${runId}`)
  },

  async getRunChecks(runId: string): Promise<CheckRun[]> {
    return request<CheckRun[]>(`/api/v1/runs/${runId}/checks`)
  },

  async getRunEvidence(runId: string, limit = 50): Promise<EvidenceItem[]> {
    const res = await request<{ items: EvidenceItem[] }>(
      `/api/v1/runs/${runId}/evidence?limit=${limit}`
    )
    return res.items
  },

  async getCheckEvidence(checkId: string, limit = 50): Promise<EvidenceItem[]> {
    const res = await request<{ items: EvidenceItem[] }>(
      `/api/v1/checks/${checkId}/evidence?limit=${limit}`
    )
    return res.items
  },

  async getRunAnalysis(runId: string): Promise<Analysis> {
    return request<Analysis>(`/api/v1/runs/${runId}/analysis`)
  },

  async requestAnalysis(runId: string): Promise<Analysis> {
    return request<Analysis>(`/api/v1/runs/${runId}/analysis/request`, {
      method: 'POST',
    })
  },

  async retryAnalysis(runId: string): Promise<Analysis> {
    return request<Analysis>(`/api/v1/runs/${runId}/analysis/retry`, {
      method: 'POST',
    })
  },

  async cancelRun(runId: string): Promise<Run> {
    return request<Run>(`/api/v1/runs/${runId}/cancel`, {
      method: 'POST',
    })
  },

  async getRunPatchAttempts(runId: string): Promise<PatchAttempt[]> {
    return request<PatchAttempt[]>(`/api/v1/runs/${runId}/patch-attempts`)
  },

  async createPatchAttempt(
    runId: string,
    customInstructions?: string
  ): Promise<PatchAttempt> {
    return request<PatchAttempt>(`/api/v1/runs/${runId}/patch-attempts`, {
      method: 'POST',
      body: JSON.stringify({ custom_instructions: customInstructions }),
    })
  },

  async getCurrentUser(): Promise<CurrentUserResponse> {
    return request<CurrentUserResponse>('/api/v1/auth/me')
  },

  async logout(): Promise<{ status: string }> {
    return request<{ status: string }>('/api/v1/auth/logout', {
      method: 'POST',
    })
  },

  getLoginUrl(): string {
    return '/api/v1/auth/github/login'
  },
}

