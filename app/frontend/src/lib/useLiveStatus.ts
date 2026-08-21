import { useEffect, useRef, useState } from 'react'
import { TENANT_BASE } from '@/lib/api'

/**
 * useLiveStatus — keep candidate / demand-file / document statuses fresh with a
 * realtime SSE stream AND an automatic polling fallback.
 *
 * This app has no Supabase/Postgres realtime channel; instead the backend
 * exposes:
 *   • GET /api/realtime/status-stream   (SSE — emits an opaque "version" token
 *     whenever any tracked status row changes), and
 *   • GET /api/realtime/status-snapshot (one-shot token — the POLLING fallback).
 *
 * Behaviour:
 *   1. Try to open the SSE stream. On every `status` event whose token differs
 *      from the last seen token, call `onChange()` (callers refetch their list).
 *   2. If the stream errors / closes (proxy buffering, network drop, etc.), we
 *      transparently switch to polling the snapshot endpoint every
 *      `pollIntervalMs`, still firing `onChange()` on token change — so statuses
 *      never freeze even when realtime is unavailable.
 *   3. When the stream recovers, polling is stopped again.
 *
 * `transport` is exposed for optional UI (e.g. a subtle "live / polling" dot).
 */
export type LiveTransport = 'connecting' | 'live' | 'polling' | 'offline'

interface Options {
  /** ms between polls when in fallback mode (default 5000). */
  pollIntervalMs?: number
  /** disable entirely (e.g. page not visible) */
  enabled?: boolean
}

export function useLiveStatus(
  onChange: () => void,
  opts: Options = {},
): { transport: LiveTransport } {
  const { pollIntervalMs = 5000, enabled = true } = opts
  const [transport, setTransport] = useState<LiveTransport>('connecting')

  const cbRef = useRef(onChange)
  useEffect(() => { cbRef.current = onChange }, [onChange])

  const lastVersion = useRef<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    let es: EventSource | null = null
    let pollTimer: number | undefined
    let stopped = false

    const apply = (version: string | null | undefined) => {
      if (!version) return
      if (lastVersion.current === null) {
        // First observation just establishes a baseline — don't refetch.
        lastVersion.current = version
        return
      }
      if (version !== lastVersion.current) {
        lastVersion.current = version
        cbRef.current()
      }
    }

    // ---- polling fallback ------------------------------------------------
    const poll = async () => {
      try {
        const res = await fetch(`${TENANT_BASE}/api/realtime/status-snapshot`, {
          credentials: 'include',
          headers: { Accept: 'application/json' },
        })
        if (!res.ok) throw new Error(String(res.status))
        const data = await res.json()
        apply(data.version)
        if (!stopped) setTransport('polling')
      } catch {
        if (!stopped) setTransport('offline')
      }
    }

    const startPolling = () => {
      if (pollTimer != null) return
      if (!stopped) setTransport('polling')
      // Immediate poll, then on interval.
      void poll()
      pollTimer = window.setInterval(poll, pollIntervalMs)
    }

    const stopPolling = () => {
      if (pollTimer != null) {
        window.clearInterval(pollTimer)
        pollTimer = undefined
      }
    }

    // ---- primary SSE transport ------------------------------------------
    const startStream = () => {
      // EventSource can't set headers; auth rides on the HttpOnly cookie.
      try {
        es = new EventSource(`${TENANT_BASE}/api/realtime/status-stream`, { withCredentials: true })
      } catch {
        startPolling()
        return
      }

      es.addEventListener('open', () => {
        if (stopped) return
        setTransport('live')
        stopPolling() // stream is healthy — no need to poll
      })

      es.addEventListener('status', (ev: MessageEvent) => {
        if (stopped) return
        try {
          const data = JSON.parse(ev.data)
          apply(data.version)
        } catch { /* ignore malformed frame */ }
      })

      es.addEventListener('error', () => {
        if (stopped) return
        // Stream dropped — degrade to polling. EventSource auto-reconnects, so
        // if the stream recovers, its 'open' handler will stop polling again.
        startPolling()
      })
    }

    startStream()

    return () => {
      stopped = true
      stopPolling()
      if (es) es.close()
    }
  }, [enabled, pollIntervalMs])

  return { transport }
}

export default useLiveStatus
