/**
 * Toolbar field search + add combobox (layout A).
 * Replaces the old fixed left "palette" column: the user searches all data
 * fields inline in the top toolbar and clicks a result (or presses Enter on the
 * first) to drop it onto the canvas. Primitive inserts (text/checkbox/barcode/
 * trade table) live alongside as quick buttons.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Palette } from './types'
import { iconForType, token } from './types'
import type { PaletteDragPayload } from './PalettePanel'

interface Props {
  palette: Palette
  placedKeys: Set<string>
  onInsertField: (p: PaletteDragPayload) => void
  onInsertPrimitive: (kind: 'text' | 'checkbox' | 'barcode' | 'trade_table') => void
  onAddCustomText: (text: string) => void
}

export default function FieldSearchAdd({ palette, placedKeys, onInsertField, onInsertPrimitive, onAddCustomText }: Props) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement | null>(null)

  const q = query.trim().toLowerCase()
  const results = useMemo(() => {
    const flat = (palette.groups || []).flatMap((g) => (g.fields || []).map((f) => ({ ...f, group: g.group })))
    if (!q) return flat.slice(0, 40)
    return flat.filter((f) => f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q)).slice(0, 40)
  }, [palette, q])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const pick = (f: { key: string; label: string; field_type: string }) => {
    onInsertField({ key: f.key, label: f.label, ftype: f.field_type })
    setOpen(false)
    setQuery('')
  }

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const first = results[0]
      if (first) { pick(first); return }
      const v = query.trim()
      if (v) { onAddCustomText(v); setQuery(''); setOpen(false) }
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div ref={boxRef} className="relative flex items-center gap-2">
      {/* Search-to-add field combobox */}
      <div className="relative">
        <i className="fa-solid fa-magnifying-glass absolute left-2.5 top-2 text-slate-400 text-xs" />
        <input
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKey}
          type="text"
          placeholder="Search & add field…  (Enter = add first / custom text)"
          className="w-[280px] pl-7 pr-2 py-1.5 text-xs border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white"
        />
        {open && (
          <div className="absolute z-40 left-0 top-full mt-1 w-[340px] bg-white border border-slate-200 rounded-lg shadow-xl max-h-72 overflow-y-auto">
            {results.length === 0 ? (
              <div className="px-3 py-3 text-xs text-slate-400">
                No matching field. Press <b>Enter</b> to add “{query.trim()}” as static text.
              </div>
            ) : (
              results.map((f) => {
                const placed = placedKeys.has(f.key)
                return (
                  <button
                    key={`${f.group}:${f.key}`}
                    type="button"
                    onMouseDown={(e) => { e.preventDefault(); pick(f) }}
                    className="w-full text-left px-3 py-1.5 hover:bg-blue-50 border-b border-slate-100 last:border-0 flex items-start gap-2 group"
                  >
                    <i className={`${iconForType(f.field_type)} w-4 text-center text-slate-400 group-hover:text-blue-500 mt-0.5`} />
                    <span className="flex-1 min-w-0 flex flex-col leading-tight">
                      <span className="truncate text-xs font-medium text-slate-800">{f.label}</span>
                      <span className="truncate text-[10px] font-mono text-slate-400">{token(f.key)} · {f.group}</span>
                    </span>
                    {placed
                      ? <i className="fa-solid fa-check text-[10px] text-emerald-500 mt-1" title="Already on this template" />
                      : <i className="fa-solid fa-plus text-[10px] text-slate-300 group-hover:text-blue-500 mt-1" />}
                  </button>
                )
              })
            )}
          </div>
        )}
      </div>

      {/* Primitive quick-inserts */}
      <div className="inline-flex items-center gap-1">
        <button type="button" onClick={() => onInsertPrimitive('text')} title="Insert a free-form text box" className="dsg-tool-btn"><i className="fa-regular fa-square" /></button>
        <button type="button" onClick={() => onInsertPrimitive('checkbox')} title="Insert a checkbox" className="dsg-tool-btn"><i className="fa-regular fa-square-check" /></button>
        <button type="button" onClick={() => onInsertPrimitive('barcode')} title="Insert a barcode" className="dsg-tool-btn"><i className="fa-solid fa-barcode" /></button>
        <button type="button" onClick={() => onInsertPrimitive('trade_table')} title="Insert a trade table" className="dsg-tool-btn"><i className="fa-solid fa-table" /></button>
      </div>
    </div>
  )
}
