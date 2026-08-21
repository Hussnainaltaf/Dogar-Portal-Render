/**
 * Floating properties popover (layout A).
 * Wraps the existing PropertiesPanel in a draggable, positioned card that
 * appears near the selected field instead of a fixed right-hand column. The
 * user can drag it by its header bar so it never permanently obscures the
 * canvas, and close it with the × (which also deselects).
 */
import { useEffect, useRef, useState } from 'react'
import type { DesignerField, Palette } from './types'
import PropertiesPanel from './PropertiesPanel'

interface Props {
  field: DesignerField
  palette: Palette
  pageW: number
  pageH: number
  initial: { x: number; y: number }
  onPatch: (patch: Partial<DesignerField>) => void
  onPatchMeta: (key: string, value: unknown) => void
  onDelete: () => void
  onDuplicate: () => void
  onClose: () => void
}

const CARD_W = 320

export default function FloatingProperties(props: Props) {
  const { field, initial, onClose } = props
  const [pos, setPos] = useState(initial)
  const ref = useRef<HTMLDivElement | null>(null)

  // Re-anchor when a DIFFERENT field is selected (not on every prop change).
  useEffect(() => {
    const maxX = window.innerWidth - CARD_W - 12
    const maxY = window.innerHeight - 120
    setPos({ x: Math.min(Math.max(12, initial.x), Math.max(12, maxX)), y: Math.min(Math.max(72, initial.y), Math.max(72, maxY)) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [field._uid])

  const beginDrag = (e: React.PointerEvent) => {
    const startX = e.clientX, startY = e.clientY
    const p0 = { ...pos }
    const el = e.currentTarget as HTMLElement
    try { el.setPointerCapture(e.pointerId) } catch { /* noop */ }
    const move = (ev: PointerEvent) => {
      const nx = Math.min(Math.max(4, p0.x + (ev.clientX - startX)), window.innerWidth - CARD_W - 4)
      const ny = Math.min(Math.max(4, p0.y + (ev.clientY - startY)), window.innerHeight - 60)
      setPos({ x: nx, y: ny })
    }
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div
      ref={ref}
      className="fixed z-[75] bg-white border border-slate-200 rounded-xl shadow-2xl flex flex-col"
      style={{ left: pos.x, top: pos.y, width: CARD_W, maxHeight: 'calc(100vh - 96px)' }}
    >
      <div
        onPointerDown={beginDrag}
        className="flex items-center justify-between px-3 py-2 border-b border-slate-100 bg-slate-50 rounded-t-xl cursor-move select-none"
      >
        <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wide inline-flex items-center gap-1.5">
          <i className="fa-solid fa-grip-vertical text-slate-300" /> Properties
        </h3>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-700" title="Close (deselect)">
          <i className="fa-solid fa-xmark" />
        </button>
      </div>
      <div className="overflow-y-auto" style={{ overscrollBehavior: 'contain' }}>
        <PropertiesPanel
          field={props.field}
          palette={props.palette}
          pageW={props.pageW}
          pageH={props.pageH}
          onPatch={props.onPatch}
          onPatchMeta={props.onPatchMeta}
          onDelete={props.onDelete}
          onDuplicate={props.onDuplicate}
        />
      </div>
    </div>
  )
}
