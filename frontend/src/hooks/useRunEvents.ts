import { useEffect, useRef, useState, useCallback } from 'react'

export interface CheckEventItem {
  id: string
  check_key: string
  check_name: string
  status: string
  exit_code?: number | null
  duration_ms?: number | null
}

export interface AnalysisEventItem {
  id: string
  status: string
  safe_error_code?: string | null
  has_result: boolean
}

export interface RunEventSnapshot {
  run_id: string
  lifecycle_status: string
  test_outcome: string
  analysis_outcome: string
  started_at?: string | null
  completed_at?: string | null
  checks: CheckEventItem[]
  evidence_count: number
  analysis?: AnalysisEventItem | null
}

interface UseRunEventsOptions {
  enabled?: boolean
  onUpdate?: (snapshot: RunEventSnapshot) => void
  onComplete?: (status: string) => void
  onError?: (err: unknown) => void
}

export function useRunEvents(
  runId: string | null,
  options: UseRunEventsOptions = {}
) {
  const { enabled = true, onUpdate, onComplete, onError } = options

  const [isConnected, setIsConnected] = useState<boolean>(false)
  const [isStreaming, setIsStreaming] = useState<boolean>(false)
  const [lastSnapshot, setLastSnapshot] = useState<RunEventSnapshot | null>(null)
  const [lastEventAt, setLastEventAt] = useState<Date | null>(null)

  const eventSourceRef = useRef<EventSource | null>(null)
  const onUpdateRef = useRef(onUpdate)
  onUpdateRef.current = onUpdate
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete
  const onErrorRef = useRef(onError)
  onErrorRef.current = onError

  const disconnect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
      setIsConnected(false)
      setIsStreaming(false)
    }
  }, [])

  useEffect(() => {
    if (!runId || !enabled) {
      disconnect()
      return
    }

    // Only establish connection in browser environment with EventSource support
    if (typeof window === 'undefined' || !window.EventSource) {
      return
    }

    disconnect()

    const url = `/api/v1/runs/${runId}/events`
    const es = new EventSource(url, { withCredentials: true })
    eventSourceRef.current = es

    es.onopen = () => {
      setIsConnected(true)
      setIsStreaming(true)
    }

    es.addEventListener('run_update', (event) => {
      try {
        const data: RunEventSnapshot = JSON.parse(event.data)
        setLastSnapshot(data)
        setLastEventAt(new Date())
        if (onUpdateRef.current) {
          onUpdateRef.current(data)
        }
      } catch (e) {
        console.error('Error parsing SSE run_update payload:', e)
      }
    })

    es.addEventListener('complete', (event) => {
      try {
        const data = JSON.parse(event.data)
        setIsStreaming(false)
        if (onCompleteRef.current) {
          onCompleteRef.current(data.status)
        }
      } catch {
        setIsStreaming(false)
      }
      es.close()
    })

    es.onerror = (err) => {
      setIsConnected(false)
      setIsStreaming(false)
      if (onErrorRef.current) {
        onErrorRef.current(err)
      }
      es.close()
    }

    return () => {
      es.close()
      eventSourceRef.current = null
      setIsConnected(false)
      setIsStreaming(false)
    }
  }, [runId, enabled, disconnect])

  return {
    isConnected,
    isStreaming,
    lastSnapshot,
    lastEventAt,
    disconnect,
  }
}

