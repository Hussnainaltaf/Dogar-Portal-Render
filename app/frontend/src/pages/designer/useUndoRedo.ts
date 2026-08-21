/**
 * Undo/Redo hook for the designer canvas fields.
 * Stores snapshots of the fields array. Ctrl+Z undoes, Ctrl+Y / Ctrl+Shift+Z redoes.
 * Max 50 undo levels to bound memory.
 */
import { useCallback, useRef } from 'react'
import type { DesignerField } from './types'

const MAX_HISTORY = 50

export function useUndoRedo(
  getFields: () => DesignerField[],
  setFields: (fields: DesignerField[]) => void,
  markDirty: () => void,
) {
  const undoStack = useRef<string[]>([])
  const redoStack = useRef<string[]>([])

  /** Push current state onto the undo stack. Call BEFORE making a change. */
  const pushUndo = useCallback(() => {
    const snapshot = JSON.stringify(getFields())
    undoStack.current.push(snapshot)
    if (undoStack.current.length > MAX_HISTORY) undoStack.current.shift()
    // Any new action clears the redo stack
    redoStack.current = []
  }, [getFields])

  /** Undo: restore previous state, push current to redo. */
  const undo = useCallback(() => {
    const prev = undoStack.current.pop()
    if (!prev) return
    // Push current state to redo before overwriting
    redoStack.current.push(JSON.stringify(getFields()))
    try {
      setFields(JSON.parse(prev))
      markDirty()
    } catch { /* corrupt snapshot — skip */ }
  }, [getFields, setFields, markDirty])

  /** Redo: restore next state from redo stack, push current to undo. */
  const redo = useCallback(() => {
    const next = redoStack.current.pop()
    if (!next) return
    undoStack.current.push(JSON.stringify(getFields()))
    try {
      setFields(JSON.parse(next))
      markDirty()
    } catch { /* corrupt snapshot — skip */ }
  }, [getFields, setFields, markDirty])

  const canUndo = () => undoStack.current.length > 0
  const canRedo = () => redoStack.current.length > 0

  return { pushUndo, undo, redo, canUndo, canRedo }
}
