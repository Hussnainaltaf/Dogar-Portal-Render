/**
 * Canvas right-click context menu (layout A).
 * Lets the user add a field EXACTLY where they right-clicked: a search box
 * filters all data fields, and primitive inserts are offered too. The chosen
 * item is inserted at the click's PDF-point coordinates (passed back via the
 * onPick / onPrimitive callbacks which already know the drop point).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Palette } from './types'
import { iconForType, token } from './types'

export interface ContextMenuState {
  /** viewport (client) pixel position where the menu opens */
  screenX: number
  screenY: number
  /** PDF-point coordinates on the page where a field should land */
  xPt: number
  yPt: number
}

interface Props {
  state: ContextMenuState
  palette: Palette
  placedKeys: Set<string>
  onClose: () => void
  onPick: (f: { key: string; label: string; ftype: string }, xPt: number, yPt: number) => void
  onPrimitive: (kind: 'text' | 'checkbox' | 'barcode' | 'trade_table', xPt: number, yPt: number) => void
}

export default function CanvasContextMenu({ state, palette, placedKeys, onClose, onPick, onPrimitive }: Props) {
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    inputRef.current?.focus()
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose() }
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onEsc)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onEsc) }
  }, [onClose])

  const q = query.trim().toLowerCase()
  const results = useMemo(() => {
    const flat = (palette.groups || []).flatMap((g) => (g.fields || []).map((f) => ({ ...f, group: g.group })))
    if (!q) return flat.slice(0, 30)
    return flat.filter((f) => f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q)).slice(0, 30)
  }, [palette, q])

  // Keep the menu inside the viewport.
  const menuW = 300
  const menuH = 360
  const left = Math.min(state.screenX, window.innerWidth - menuW - 8)
  const top = Math.min(state.screenY, window.innerHeight - menuH - 8)

  return (
    <div
      ref={ref}
      className="fixed z-[80] w-[300px] bg-white border border-slate-200 rounded-xl shadow-2xl overflow-hidden text-[13px]"
      style={{ left: Math.max(8, left), top: Math.max(8, top) }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="px-3 py-2 bg-slate-50 border-b border-slate-100 flex items-center justify-between">
        <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wide">
          Add here · x {state.xPt.toFixed(0)} · y {state.yPt.toFixed(0)}
        </span>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><i className="fa-solid fa-xmark" /></button>
      </div>

      {/* Primitives row */}
      <div className="px-2 py-2 border-b border-slate-100 grid grid-cols-4 gap-1">
        <button type="button" onClick={() => onPrimitive('text', state.xPt, state.yPt)} className="dsg-ctx-btn" title="Text box"><i className="fa-regular fa-square" /><span>Text</span></button>
        <button type="button" onClick={() => onPrimitive('checkbox', state.xPt, state.yPt)} className="dsg-ctx-btn" title="Checkbox"><i className="fa-regular fa-square-check" /><span>Check</span></button>
        <button type="button" onClick={() => onPrimitive('barcode', state.xPt, state.yPt)} className="dsg-ctx-btn" title="Barcode"><i className="fa-solid fa-barcode" /><span>Barcode</span></button>
        <button type="button" onClick={() => onPrimitive('trade_table', state.xPt, state.yPt)} className="dsg-ctx-btn" title="Trade table"><i className="fa-solid fa-table" /><span>Table</span></button>
      </div>

      {/* Field search */}
      <div className="p-2 border-b border-slate-100">
        <div className="relative">
          <i className="fa-solid fa-magnifying-glass absolute left-2.5 top-2 text-slate-400 text-xs" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && results[0]) onPick({ key: results[0].key, label: results[0].label, ftype: results[0].field_type }, state.xPt, state.yPt) }}
            type="text"
            placeholder="Search field to add…"
            className="w-full pl-7 pr-2 py-1.5 text-xs border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none"
          />
        </div>
      </div>

      <div className="max-h-52 overflow-y-auto">
        {results.length === 0 ? (
          <div className="px-3 py-3 text-xs text-slate-400">No matching field.</div>
        ) : (
          results.map((f) => {
            const placed = placedKeys.has(f.key)
            return (
              <button
                key={`${f.group}:${f.key}`}
                type="button"
                onClick={() => onPick({ key: f.key, label: f.label, ftype: f.field_type }, state.xPt, state.yPt)}
                className="w-full text-left px-3 py-1.5 hover:bg-blue-50 border-b border-slate-100 last:border-0 flex items-start gap-2 group"
              >
                <i className={`${iconForType(f.field_type)} w-4 text-center text-slate-400 group-hover:text-blue-500 mt-0.5`} />
                <span className="flex-1 min-w-0 flex flex-col leading-tight">
                  <span className="truncate text-xs font-medium text-slate-800">{f.label}</span>
                  <span className="truncate text-[10px] font-mono text-slate-400">{token(f.key)} · {f.group}</span>
                </span>
                {placed && <i className="fa-solid fa-check text-[10px] text-emerald-500 mt-1" />}
              </button>
            )
          })
        )}
      </div>
    </div>
  )
}
