import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { api } from '@/lib/api'

/* ---------------------------------------------------------------------------
 * Global candidate search.
 *
 * Extracted from Topbar so the SAME behaviour can render in two shapes:
 *   • variant="inline" — the desktop/tablet search bar in the header.
 *   • variant="sheet"  — a full-width mobile overlay opened from the header's
 *     magnifier button. Previously the header search was simply `display:none`
 *     below 640px, so phone users had no global search at all.
 *
 * Hardening vs. the previous inline implementation:
 *   • Requests are aborted when superseded or on unmount (no state updates
 *     after teardown, no out-of-order results overwriting newer ones).
 *   • The debounce timer is cleared on unmount.
 *   • WAI-ARIA 1.2 combobox semantics with arrow-key navigation are preserved.
 * ------------------------------------------------------------------------- */

export interface SearchResult {
  id: number
  name: string
  passport_no?: string
  profession?: string
  city?: string
  district?: string
}

interface Filters { skill: string; location: string; stage: string }
const EMPTY_FILTERS: Filters = { skill: '', location: '', stage: '' }

const STAGES: [string, string][] = [
  ['', 'All Stages'],
  ['new', 'Received'],
  ['docs_pending', 'Documents Pending'],
  ['medical', 'Medical'],
  ['protector', 'Protector'],
  ['visa_stamping', 'Visa Stamping'],
  ['travel_ready', 'Clearance'],
  ['deployed', 'Departed'],
]

export default function GlobalSearch({ variant = 'inline', onClose }: {
  variant?: 'inline' | 'sheet'
  onClose?: () => void
}) {
  const navigate = useNavigate()
  const isSheet = variant === 'sheet'

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [showResults, setShowResults] = useState(false)
  const [showFilters, setShowFilters] = useState(false)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [activeIdx, setActiveIdx] = useState(-1)

  const debounce = useRef<number | undefined>(undefined)
  const abortRef = useRef<AbortController | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const filtersWrapRef = useRef<HTMLDivElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Autofocus the sheet's input the moment it opens (phones expect the keyboard).
  useEffect(() => { if (isSheet) inputRef.current?.focus() }, [isSheet])

  // Cancel any pending timer / in-flight request when this instance goes away.
  useEffect(() => () => {
    window.clearTimeout(debounce.current)
    abortRef.current?.abort()
  }, [])

  const runSearch = useCallback((q: string, f: Filters) => {
    if (!q.trim() && !f.skill && !f.location && !f.stage) {
      abortRef.current?.abort()
      setResults([])
      setShowResults(false)
      return
    }
    // Supersede the previous request so a slow earlier response can never
    // overwrite a newer one (classic autocomplete race).
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setSearching(true)
    setShowResults(true)
    setActiveIdx(-1)

    const params: Record<string, string> = { skip: '0', limit: '8' }
    if (q.trim()) params.q = q.trim()
    if (f.skill) params.skill = f.skill
    if (f.location) params.location = f.location
    if (f.stage) params.status = f.stage

    api.get('/candidates/', { params, signal: controller.signal })
      .then(({ data }) => setResults(data.items || data.candidates || data || []))
      .catch((err) => { if (!axios.isCancel(err)) setResults([]) })
      .finally(() => { if (!controller.signal.aborted) setSearching(false) })
  }, [])

  const onSearchInput = (val: string) => {
    setQuery(val)
    window.clearTimeout(debounce.current)
    debounce.current = window.setTimeout(() => runSearch(val, filters), 300)
  }

  const applyFilters = (next: Filters) => { setFilters(next); runSearch(query, next) }

  const clearSearch = () => {
    abortRef.current?.abort()
    setQuery('')
    setResults([])
    setShowResults(false)
    setActiveIdx(-1)
    inputRef.current?.focus()
  }

  const openResult = useCallback((id: number) => {
    setShowResults(false)
    setActiveIdx(-1)
    onClose?.()
    navigate(`/candidates?open=${id}`)
  }, [navigate, onClose])

  // Outside-click / Escape. `pointerdown` (not `click`) so the close fires on
  // the press, BEFORE the synthetic click a tap also produces — otherwise the
  // very tap that opens a popover closes it again on touch devices.
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (showFilters && filtersWrapRef.current && !filtersWrapRef.current.contains(t)) setShowFilters(false)
      if (!isSheet && showResults && wrapRef.current && !wrapRef.current.contains(t)) setShowResults(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (showFilters) { setShowFilters(false); return }
      if (showResults) { setShowResults(false); setActiveIdx(-1); return }
      onClose?.()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [showFilters, showResults, isSheet, onClose])

  const onSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!showResults || results.length === 0) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx((i) => (i + 1) % results.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIdx((i) => (i <= 0 ? results.length - 1 : i - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setActiveIdx(0) }
    else if (e.key === 'End') { e.preventDefault(); setActiveIdx(results.length - 1) }
    else if (e.key === 'Enter' && activeIdx >= 0 && activeIdx < results.length) {
      e.preventDefault()
      openResult(results[activeIdx].id)
    }
  }

  // Keep the keyboard cursor's option in view.
  useEffect(() => {
    if (activeIdx < 0 || !resultsRef.current) return
    resultsRef.current.querySelector<HTMLButtonElement>(`#gsearch-opt-${activeIdx}`)?.scrollIntoView({ block: 'nearest' })
  }, [activeIdx])

  const activeFilterCount = (filters.skill ? 1 : 0) + (filters.location ? 1 : 0) + (filters.stage ? 1 : 0)

  return (
    <div
      ref={wrapRef}
      className={isSheet
        ? 'relative w-full flex items-center gap-2'
        : 'relative w-full max-w-xl mr-2 sm:mr-4 hidden lg:flex items-center gap-1.5 sm:gap-2'}
    >
      <div className="relative flex-1 min-w-0">
        <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm pointer-events-none" />
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => onSearchInput(e.target.value)}
          onFocus={() => query && setShowResults(true)}
          onKeyDown={onSearchKeyDown}
          placeholder={isSheet ? 'Search candidates…' : 'Search candidates by name, passport, skill, location...'}
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-expanded={showResults}
          aria-controls="gsearch-results"
          aria-autocomplete="list"
          aria-label="Search candidates"
          aria-activedescendant={activeIdx >= 0 ? `gsearch-opt-${activeIdx}` : undefined}
          className="w-full pl-9 pr-9 py-2 text-sm border border-slate-200 rounded-lg bg-white focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition"
        />
        {query && (
          <button
            type="button"
            onClick={clearSearch}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full text-slate-400 hover:text-slate-700 hover:bg-slate-100 flex items-center justify-center transition"
          >
            <i className="fa-solid fa-xmark text-xs" />
          </button>
        )}
      </div>

      {/* Filters */}
      <div className="relative shrink-0" ref={filtersWrapRef}>
        <button
          type="button"
          onClick={() => setShowFilters((s) => !s)}
          aria-expanded={showFilters}
          aria-label="Search filters"
          className="px-3 py-2 min-h-[38px] text-xs border border-slate-200 rounded-lg bg-slate-50 text-slate-700 hover:bg-slate-100 flex items-center gap-1.5 font-semibold transition"
        >
          <i className="fa-solid fa-filter text-slate-400" />
          <span className="hidden sm:inline">Filters</span>
          {activeFilterCount > 0 && (
            <span className="ml-0.5 inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full text-[10px] font-bold text-white brand-gradient">
              {activeFilterCount}
            </span>
          )}
        </button>
        {showFilters && (
          <div className="absolute right-0 top-full mt-2 w-72 max-w-[calc(100vw-2rem)] bg-white border border-slate-200 rounded-xl shadow-2xl z-[70] p-4 space-y-3">
            <div className="text-xs font-bold text-slate-700 uppercase tracking-widest border-b pb-1.5 flex justify-between items-center">
              <span>Search Filters</span>
              <button type="button" onClick={() => setShowFilters(false)} className="text-slate-400 hover:text-slate-600" aria-label="Close filters">
                <i className="fa-solid fa-xmark" />
              </button>
            </div>
            <FilterField label="Trade/Skill Role" value={filters.skill} placeholder="e.g. Mason, Electrician..."
              onChange={(v) => applyFilters({ ...filters, skill: v })} />
            <FilterField label="Location/City/District" value={filters.location} placeholder="e.g. Rawalpindi, Lahore..."
              onChange={(v) => applyFilters({ ...filters, location: v })} />
            <div>
              <label className="block text-[11px] font-semibold text-slate-500 uppercase mb-1" htmlFor="gsearch-stage">Candidate Stage</label>
              <select
                id="gsearch-stage"
                value={filters.stage}
                onChange={(e) => applyFilters({ ...filters, stage: e.target.value })}
                className="w-full px-2 py-1.5 text-xs border border-slate-200 rounded-md bg-white focus:outline-none focus:border-blue-500"
              >
                {STAGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </div>
            <div className="flex justify-end pt-2 border-t mt-3">
              <button type="button" onClick={() => applyFilters(EMPTY_FILTERS)}
                className="text-[11px] font-semibold text-blue-600 hover:text-blue-700 hover:underline">Reset Filters</button>
            </div>
          </div>
        )}
      </div>

      {/* Results */}
      {showResults && (
        <div
          id="gsearch-results"
          className="absolute left-0 right-0 top-full mt-2 bg-white border border-slate-200 rounded-xl shadow-2xl z-[70] overflow-hidden max-h-[min(480px,60vh)] flex flex-col"
        >
          {searching ? (
            <div className="px-4 py-6 text-sm text-slate-500" role="status" aria-live="polite">
              <i className="fa-solid fa-spinner fa-spin mr-1.5 text-blue-600" /> Searching candidates…
            </div>
          ) : results.length === 0 ? (
            <div className="px-4 py-8 text-center text-slate-400" role="status">
              <i className="fa-regular fa-face-frown text-xl block mb-2 opacity-60" />
              No candidates found
            </div>
          ) : (
            <div ref={resultsRef} role="listbox" aria-label="Candidate search results" className="overflow-y-auto divide-y divide-slate-100">
              {results.map((r, i) => {
                const isActive = i === activeIdx
                return (
                  <button
                    key={r.id}
                    id={`gsearch-opt-${i}`}
                    role="option"
                    aria-selected={isActive}
                    onMouseEnter={() => setActiveIdx(i)}
                    onClick={() => openResult(r.id)}
                    className={`w-full text-left px-4 py-3 transition ${isActive ? 'bg-blue-50' : 'hover:bg-slate-50'}`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-semibold text-slate-800 text-sm truncate">{r.name}</span>
                      {r.passport_no && <span className="font-mono text-[11px] text-slate-500 shrink-0">{r.passport_no}</span>}
                    </div>
                    <div className="flex items-center gap-4 text-[11px] text-slate-500 mt-0.5">
                      <span className="truncate pr-2"><i className="fa-solid fa-briefcase text-slate-400 mr-0.5" /> {r.profession || '—'}</span>
                      <span className="truncate"><i className="fa-solid fa-location-dot text-slate-400 mr-0.5" /> {r.city || r.district || '—'}</span>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FilterField({ label, value, placeholder, onChange }: {
  label: string; value: string; placeholder: string; onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="block text-[11px] font-semibold text-slate-500 uppercase mb-1">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-2.5 py-1.5 text-xs border border-slate-200 rounded-md focus:outline-none focus:border-blue-500"
      />
    </div>
  )
}
