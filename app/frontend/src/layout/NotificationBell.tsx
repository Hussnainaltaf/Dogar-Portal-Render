import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'

/* ------------------------------------------------------------------ *
 * NotificationBell — real-time reminders/alerts dropdown in the header.
 *
 * - Polls GET /api/reminders/summary every 30s for the unread badge.
 * - Loads GET /api/reminders?state=active on open (severity-sorted).
 * - Mark-read / dismiss / mark-all-read / "Scan now" actions wired to
 *   the reminders API. Clicking a reminder opens the linked record.
 * ------------------------------------------------------------------ */

interface Reminder {
  id: number
  kind: string
  severity: 'critical' | 'warning' | 'info' | string
  title: string
  body?: string
  candidate_id?: number | null
  candidate_name?: string | null
  demand_id?: number | null
  demand_file_no?: string | null
  is_read?: boolean
  is_dismissed?: boolean
  due_at?: string | null
  created_at?: string | null
}

const POLL_MS = 30_000

function sevStyle(sev: string): { dot: string; icon: string } {
  switch (sev) {
    case 'critical': return { dot: 'bg-red-500', icon: 'fa-circle-exclamation text-red-500' }
    case 'warning': return { dot: 'bg-amber-500', icon: 'fa-triangle-exclamation text-amber-500' }
    default: return { dot: 'bg-blue-500', icon: 'fa-circle-info text-blue-500' }
  }
}

function timeAgo(iso?: string | null): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (s < 60) return 'just now'
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

export default function NotificationBell() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [unread, setUnread] = useState(0)
  const [items, setItems] = useState<Reminder[]>([])
  const [loading, setLoading] = useState(false)
  const [scanning, setScanning] = useState(false)
  const pollRef = useRef<number | undefined>(undefined)
  const rootRef = useRef<HTMLDivElement>(null)

  // Close on Escape. (Outside-click is handled by the portal backdrop below —
  // see the note on why the dropdown is portalled to <body>.)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  const loadSummary = useCallback(async () => {
    try {
      const { data } = await api.get('/reminders/summary')
      setUnread(Number(data?.unread || 0))
    } catch {
      /* silent — bell just shows no badge */
    }
  }, [])

  const loadList = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await api.get('/reminders', { params: { state: 'active', limit: '30' } })
      setItems(data?.items || [])
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  // Poll the summary badge on mount + every 30s.
  useEffect(() => {
    void loadSummary()
    pollRef.current = window.setInterval(loadSummary, POLL_MS)
    return () => window.clearInterval(pollRef.current)
  }, [loadSummary])

  // Load the full list whenever the dropdown opens.
  useEffect(() => {
    if (open) void loadList()
  }, [open, loadList])

  const hrefFor = (r: Reminder): string | null => {
    if (r.candidate_id) return `/candidates?open=${r.candidate_id}`
    if (r.demand_id) return `/demands/${r.demand_id}`
    return null
  }

  const openReminder = async (r: Reminder) => {
    if (!r.is_read) {
      try { await api.post(`/reminders/${r.id}/read`) } catch { /* noop */ }
      setUnread((u) => Math.max(0, u - 1))
      setItems((prev) => prev.map((x) => (x.id === r.id ? { ...x, is_read: true } : x)))
    }
    const href = hrefFor(r)
    if (href) { setOpen(false); navigate(href) }
  }

  const dismiss = async (e: React.MouseEvent, r: Reminder) => {
    e.stopPropagation()
    try { await api.post(`/reminders/${r.id}/dismiss`) } catch { /* noop */ }
    setItems((prev) => prev.filter((x) => x.id !== r.id))
    if (!r.is_read) setUnread((u) => Math.max(0, u - 1))
  }

  const markAllRead = async () => {
    try { await api.post('/reminders/read-all') } catch { /* noop */ }
    setUnread(0)
    setItems((prev) => prev.map((x) => ({ ...x, is_read: true })))
  }

  const scanNow = async () => {
    setScanning(true)
    try {
      await api.post('/reminders/scan')
      await Promise.all([loadSummary(), loadList()])
    } catch {
      /* noop */
    } finally {
      setScanning(false)
    }
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}
        className="relative min-w-[40px] min-h-[40px] w-10 h-10 rounded-full hover:bg-slate-100 active:bg-slate-200 flex items-center justify-center transition touch-manipulation"
        title="Reminders & Alerts"
        aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
        aria-expanded={open}
      >
        <i className="fa-regular fa-bell text-slate-600 text-lg" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center border-2 border-white">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {/* The dropdown is PORTALLED to <body>. The topbar uses `backdrop-filter`
          (backdrop-blur), which on mobile Chrome/Android traps `position:fixed`
          descendants inside the filtered ancestor's containing block — so a
          fixed panel rendered here would mis-position or become non-interactive
          ("bell not clickable" bug). Rendering through a portal escapes that
          stacking/containing context entirely. A full-screen transparent
          backdrop handles outside-tap close reliably on touch. */}
      {open && createPortal(
        <>
          <div
            className="fixed inset-0 z-[90]"
            aria-hidden="true"
            onClick={() => setOpen(false)}
          />
          <div className="fixed inset-x-3 top-[4.5rem] sm:left-auto sm:right-4 sm:w-[360px] sm:max-w-[calc(100vw-2rem)] bg-white border border-slate-200 rounded-xl shadow-2xl z-[95] overflow-hidden flex flex-col max-h-[75vh]" role="dialog" aria-label="Notifications">
          {/* Header */}
          <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between bg-slate-50">
            <div className="font-bold text-slate-800 text-sm flex items-center gap-2">
              <i className="fa-regular fa-bell text-blue-600" /> Notifications
              {unread > 0 && <span className="text-[11px] font-semibold text-red-600">({unread} new)</span>}
            </div>
            <div className="flex items-center gap-2">
              <button onClick={scanNow} disabled={scanning} title="Scan for new alerts"
                className="text-[11px] font-semibold text-slate-500 hover:text-blue-600 disabled:opacity-50">
                <i className={`fa-solid fa-arrows-rotate ${scanning ? 'fa-spin' : ''} mr-1`} />Scan
              </button>
              {unread > 0 && (
                <button onClick={markAllRead} className="text-[11px] font-semibold text-blue-600 hover:text-blue-700">
                  Mark all read
                </button>
              )}
            </div>
          </div>

          {/* List */}
          <div className="overflow-y-auto divide-y divide-slate-100">
            {loading ? (
              <div className="px-4 py-10 text-center text-slate-400 text-sm">
                <i className="fa-solid fa-spinner fa-spin text-blue-600 mr-1.5" /> Loading…
              </div>
            ) : items.length === 0 ? (
              <div className="px-4 py-10 text-center text-slate-400">
                <i className="fa-regular fa-bell-slash text-2xl block mb-2 opacity-50" />
                <div className="text-sm font-medium">You're all caught up</div>
                <div className="text-[11px] mt-0.5">No active reminders or alerts.</div>
              </div>
            ) : (
              items.map((r) => {
                const s = sevStyle(r.severity)
                return (
                  <button
                    key={r.id}
                    onClick={() => openReminder(r)}
                    className={`w-full text-left px-4 py-3 hover:bg-slate-50 transition flex gap-3 ${r.is_read ? '' : 'bg-blue-50/40'}`}
                  >
                    <i className={`fa-solid ${s.icon} mt-0.5`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        {!r.is_read && <span className={`w-2 h-2 rounded-full ${s.dot} shrink-0`} />}
                        <span className={`text-[13px] truncate ${r.is_read ? 'text-slate-700' : 'font-semibold text-slate-900'}`}>{r.title}</span>
                      </div>
                      {r.body && <div className="text-[12px] text-slate-500 mt-0.5 line-clamp-2">{r.body}</div>}
                      <div className="text-[11px] text-slate-400 mt-1 flex items-center gap-2">
                        {(r.candidate_name || r.demand_file_no) && (
                          <span className="truncate">
                            <i className="fa-solid fa-link mr-1" />
                            {r.candidate_name || `File ${r.demand_file_no}`}
                          </span>
                        )}
                        <span className="ml-auto shrink-0">{timeAgo(r.created_at)}</span>
                      </div>
                    </div>
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={(e) => dismiss(e, r)}
                      title="Dismiss"
                      className="text-slate-300 hover:text-red-500 shrink-0 self-start mt-0.5"
                    >
                      <i className="fa-solid fa-xmark" />
                    </span>
                  </button>
                )
              })
            )}
          </div>

          {/* Footer */}
          <div className="px-4 py-2.5 border-t border-slate-100 bg-slate-50 text-center">
            <button
              onClick={() => { void loadList(); void loadSummary() }}
              className="text-[12px] font-semibold text-blue-600 hover:text-blue-700"
            >
              <i className="fa-solid fa-arrows-rotate mr-1 text-[10px]" /> Refresh
            </button>
          </div>
        </div>
        </>,
        document.body,
      )}
    </div>
  )
}
