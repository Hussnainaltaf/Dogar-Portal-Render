import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { useAuth } from '@/auth/AuthContext'
import { ensureChart } from '@/lib/chart'
import { greeting } from '@/lib/format'
import { TENANT_INFO } from '@/lib/api'
import { brandFullName } from '@/lib/branding'

interface Stats {
  totals: { clients: number; demands: number; candidates: number; agents: number; visa_quota: number }
  candidates_status: { pending: number; processing: number; issued: number; deployed: number }
  top_trades: { trade: string; qty: number }[]
  last_updated: string
}

const QUICK = [
  { to: '/demands', icon: 'fa-plus', label: 'New Demand File' },
  { to: '/clients', icon: 'fa-plus', label: 'Add Client' },
  { to: '/candidates', icon: 'fa-plus', label: 'New Candidate' },
  // v13 — was `/agents` "Add Sponsor", a DEAD link: that route was removed in
  // 0ccaaa1 and the tile just dumped users on the catch-all. Replaced with a
  // live destination that serves the same "jump to a directory" intent.
  { to: '/documents', icon: 'fa-file-lines', label: 'Documents' },
  { to: '/visa-categories', icon: 'fa-list', label: 'Visa Categories' },
  { to: '/settings', icon: 'fa-gear', label: 'Settings' },
]

// FIX 9 — greeting() was previously duplicated here and in Topbar.tsx with
// identical logic. Moved to src/lib/format.ts as a single source of truth
// and imported in both call-sites so future changes stay in sync.

export default function Dashboard() {
  const { user } = useAuth()
  const [stats, setStats] = useState<Stats | null>(null)
  const [year, setYear] = useState(new Date().getFullYear())
  const lineRef = useRef<HTMLCanvasElement>(null)
  const donutRef = useRef<HTMLCanvasElement>(null)
  const barRef = useRef<HTMLCanvasElement>(null)
  const charts = useRef<any[]>([])

  const loadStats = () => api.get<Stats>('/dashboard/stats').then(({ data }) => setStats(data)).catch(() => {})

  // Stats auto-refresh, but only while the tab is actually being looked at.
  // Previously this polled every 60s forever — including in background tabs
  // left open overnight — which burned battery on phones and hit the API for
  // nothing. Now the interval is torn down on hide and a fresh fetch runs the
  // moment the tab comes back, so the data a user SEES is never stale.
  useEffect(() => {
    let timer: number | undefined

    const stop = () => {
      if (timer !== undefined) { window.clearInterval(timer); timer = undefined }
    }
    const start = () => {
      stop()
      timer = window.setInterval(loadStats, 60000)
    }

    const onVisibility = () => {
      if (document.hidden) { stop(); return }
      void loadStats()
      start()
    }

    void loadStats()
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Donut + bar when stats arrive.
  useEffect(() => {
    if (!stats) return
    let cancelled = false
    ensureChart().then((Chart) => {
      if (cancelled) return
      const s = stats.candidates_status
      if (donutRef.current) {
        charts.current.push(new Chart(donutRef.current, {
          type: 'doughnut',
          data: {
            labels: ['Pending', 'Processing', 'Visa Issued', 'Deployed'],
            datasets: [{ data: [s.pending, s.processing, s.issued, s.deployed], backgroundColor: ['#f59e0b', '#3b82f6', '#2563eb', '#10b981'], borderWidth: 0 }],
          },
          options: { responsive: true, maintainAspectRatio: false, cutout: '62%', plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 } } } } },
        }))
      }
      if (barRef.current && stats.top_trades.length) {
        charts.current.push(new Chart(barRef.current, {
          type: 'bar',
          data: { labels: stats.top_trades.map((t) => t.trade), datasets: [{ label: 'Quota', data: stats.top_trades.map((t) => t.qty), backgroundColor: '#2563eb', borderRadius: 6 }] },
          options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#f1f5f9' } }, y: { grid: { display: false } } } },
        }))
      }
    })
    return () => { cancelled = true; charts.current.forEach((c) => c.destroy()); charts.current = [] }
  }, [stats])

  // Monthly line chart (reloads on year change).
  const lineChart = useRef<any>(null)
  useEffect(() => {
    let cancelled = false
    Promise.all([ensureChart(), api.get('/dashboard/monthly', { params: { year } })]).then(([Chart, res]) => {
      if (cancelled || !lineRef.current) return
      const d = res.data
      lineChart.current?.destroy()
      lineChart.current = new Chart(lineRef.current, {
        type: 'line',
        data: {
          labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
          datasets: [
            { label: 'Candidates', data: d.candidates, borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,.1)', tension: .35, fill: true },
            { label: 'Demands', data: d.demands, borderColor: '#16a34a', backgroundColor: 'rgba(22,163,74,.08)', tension: .35, fill: true },
          ],
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } }, scales: { y: { grid: { color: '#f1f5f9' }, beginAtZero: true }, x: { grid: { display: false } } } },
      })
    })
    return () => { cancelled = true; lineChart.current?.destroy() }
  }, [year])

  const t = stats?.totals
  const cs = stats?.candidates_status
  const years = Array.from({ length: 5 }, (_, i) => new Date().getFullYear() - i)
  const fmt = (n?: number) => (n == null ? '—' : n.toLocaleString())

  return (
    <div className="space-y-6">
      {/* Greeting */}
      <div className="flex items-start justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">{greeting()}, {user?.name || 'Administrator'}!</h1>
          {/* MEASURED LEAK 2026-08-19, seen in a screenshot of the branded
              tenant dashboard: the colours and typeface were correctly the
              office's, and the subtitle underneath still read "Overview of
              Dogar Trading Corporation". A partner office's staff were shown
              another company's name on their own home page.

              brandFullName() already existed and already falls back to the
              Dogar name when no tenant is resolved, so the root portal is
              unchanged -- the string was simply hardcoded here instead of
              going through it. */}
          <p className="text-sm text-slate-500 mt-0.5">Overview of {brandFullName(TENANT_INFO)}</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400 mt-1">
          <span className="inline-flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            Live · auto-refresh every 60s
          </span>
        </div>
      </div>

      {/* Quick actions */}
      <div>
        <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Quick Actions</p>
        <div className="flex flex-wrap gap-2">
          {QUICK.map((q) => (
            /* btn-secondary carries the frosted treatment (--glass-3 + blur-sm
               + --depth-1). Its own bg/border/hover utilities are removed for
               the same source-order reason as the cards above; px-3.5/py-2 are
               kept so the pill keeps its current size. */
            <Link key={q.label} to={q.to} className="btn btn-secondary inline-flex items-center gap-1.5 px-3.5 py-2 text-sm font-medium">
              <i className={`fa-solid ${q.icon} text-xs text-slate-400`} /> {q.label}
            </Link>
          ))}
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard to="/candidates" label="Candidates" value={fmt(t?.candidates)} icon="fa-users" tint="blue" />
        <StatCard to="/clients" label="Clients" value={fmt(t?.clients)} icon="fa-building" tint="green" />
        <StatCard to="/demands" label="Demand Files" value={fmt(t?.demands)} icon="fa-file-lines" tint="blue" />
        <StatCard to="/visa-categories" label="Visa Quota" value={fmt(t?.visa_quota)} icon="fa-passport" tint="amber" />
      </div>

      {/* Stage badges */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StageBadge color="amber" icon="fa-clock-rotate-left" label="Pending" value={fmt(cs?.pending)} />
        <StageBadge color="blue" icon="fa-spinner" label="Processing" value={fmt(cs?.processing)} />
        <StageBadge color="blue" icon="fa-id-card" label="Visa Issued" value={fmt(cs?.issued)} />
        <StageBadge color="emerald" icon="fa-plane-departure" label="Deployed" value={fmt(cs?.deployed)} />
      </div>

      {/* Charts */}
      {/* GLASS (v19, 2026-08-17): these surfaces used to be raw
          `bg-white p-5 rounded-xl border border-slate-200 shadow-sm`, which is
          why the Dashboard was the ONE page the glassmorphism layer never
          reached -- a CSS-only layer keyed on `.card` cannot touch a surface
          built from utilities. verify_glass_ui.py measured 0 glass surfaces
          here while every other page reported 4-5.

          The utilities are REMOVED, not merely joined by `card`. app.css is
          linked at index.html:77 and the compiled Tailwind bundle at :103, so
          at equal specificity (both single classes) the utility wins on source
          order: `bg-white` would have overridden `background: var(--glass-2)`,
          `shadow-sm` would have overridden `var(--depth-2)`, and
          `border-slate-200` the glass rim -- the same cascade trap that let
          `bg-slate-50` on <body> silently defeat the ambient canvas.
          `p-5` is kept because `.card` deliberately sets no padding. */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="card md:col-span-2 p-5">
          <div className="flex items-center justify-between mb-1">
            <div>
              <h3 className="text-sm font-semibold text-slate-700">Recruitment Overview</h3>
              <p className="text-[11px] text-slate-400">Demand files &amp; candidates added per month</p>
            </div>
            <select value={year} onChange={(e) => setYear(Number(e.target.value))} className="text-xs border border-slate-200 rounded px-2 py-1 text-slate-600 focus:outline-none focus:border-blue-400">
              {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
          <div style={{ height: 220 }}><canvas ref={lineRef} /></div>
        </div>
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-1">Status Breakdown</h3>
          <p className="text-[11px] text-slate-400 mb-3">Candidates by workflow stage</p>
          <div className="flex justify-center" style={{ height: 185 }}><canvas ref={donutRef} /></div>
        </div>
      </div>

      {/* Top categories bar */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-1">Top Visa Categories</h3>
        <p className="text-[11px] text-slate-400 mb-3">By assigned quota quantity</p>
        <div style={{ height: 220 }}><canvas ref={barRef} /></div>
      </div>
    </div>
  )
}

function StatCard({ to, label, value, icon, tint }: { to: string; label: string; value: string; icon: string; tint: 'blue' | 'green' | 'amber' }) {
  const tints: Record<string, string> = { blue: 'bg-blue-50 text-blue-500', green: 'bg-green-50 text-green-500', amber: 'bg-amber-50 text-amber-500' }
  return (
    // `hover:shadow-md` is dropped too: it would have beaten `.card:hover`,
    // which supplies the glass lift (--depth-3 + a brighter --glass-3 fill).
    <Link to={to} className="card p-5 block group">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">{label}</p>
          <p className="text-3xl font-bold text-slate-800 mt-1">{value}</p>
          <p className="text-xs text-blue-600 mt-1 group-hover:underline">View all →</p>
        </div>
        <div className={`w-11 h-11 rounded-lg flex items-center justify-center ${tints[tint]}`}>
          <i className={`fa-solid ${icon} text-lg`} />
        </div>
      </div>
    </Link>
  )
}

function StageBadge({ color, icon, label, value }: { color: string; icon: string; label: string; value: string }) {
  const map: Record<string, string> = {
    amber: 'bg-amber-50 border-amber-200 text-amber-500',
    blue: 'bg-blue-50 border-blue-200 text-blue-500',
    emerald: 'bg-emerald-50 border-emerald-200 text-emerald-500',
  }
  const txt: Record<string, string> = { amber: 'text-amber-600|text-amber-800', blue: 'text-blue-600|text-blue-800', emerald: 'text-emerald-600|text-emerald-800' }
  const [lbl, val] = txt[color].split('|')
  return (
    <div className={`border rounded-lg px-4 py-3 flex items-center gap-3 ${map[color]}`}>
      <i className={`fa-solid ${icon}`} />
      <div>
        <p className={`text-[10px] font-semibold uppercase tracking-wide ${lbl}`}>{label}</p>
        <p className={`text-xl font-bold ${val}`}>{value}</p>
      </div>
    </div>
  )
}
