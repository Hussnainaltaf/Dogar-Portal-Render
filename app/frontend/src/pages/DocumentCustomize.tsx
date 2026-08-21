/**
 * Document / Template Designer — React implementation (full parity port of
 * app/templates/document_customize.html).
 * ────────────────────────────────────────────────────────────────────────────
 * Layout (mirrors the reference 3-column WYSIWYG):
 *   TOP METADATA BAR  (Name · Description · Category · Attach · Status ·
 *                      Letterhead · Depositor · Notes popover)
 *   PALETTE | CANVAS (page tabs · bg · size · orientation · zoom ·
 *                     live-data preview bar · markers · status bar) | PROPERTIES
 *
 * Coordinate system — see designer/engine.ts (single source of truth):
 *   STORED  = PDF points, origin bottom-left;  CANVAS = CSS px, origin top-left
 *   cssX = x     cssY = pageH - y - height     (1 pt == 1 px at zoom 1)
 *
 * Endpoints (all under /api/documents/):
 *   GET  /designer/palette?data_source=…       grouped palette
 *   GET  /designer/backgrounds                 backgrounds + intrinsic sizes
 *   GET  /templates/:id                        template + fields
 *   PUT  /templates/:id                        template metadata
 *   POST /templates/:id/background             upload new background (aspect-lock)
 *   POST /templates/:id/fields/bulk            fields + deleted_ids
 *   GET  /templates/:id/preview-data?record_id live data preview values
 *   GET  /templates/:id/print?record_id&auto=1 print preview (new tab)
 *
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, apiError } from '../lib/api'
import { useToast } from '../components/Toast'
import type { BgFit, DesignerField, Palette, BackgroundOpt, Template } from './designer/types'
import {
  BG_FIT_VALUES, bgFilename, bgSrc, defaultHeightFor, defaultMetaFor, defaultWidthFor,
  META_FENCE_RE, newUid, parseMetaFromDescription, serializeDescription,
} from './designer/types'
import {
  CANVAS_PADDING, CANVAS_PADDING_MOBILE,
  PAGE_SIZES, PT_TO_CSS, ZOOM_MAX, ZOOM_MIN, canvasPointToPdfBox,
  clientToCanvasPoint, clampFieldBox, clampFieldPosition, fitPageZoom,
  fitZoom, ptToMm, refitCellMeta, resolvePageGeometry, round1,
} from './designer/engine'
import PalettePanel, { type PaletteDragPayload } from './designer/PalettePanel'
import PropertiesPanel from './designer/PropertiesPanel'
import FieldMarker from './designer/FieldMarker'
import ContentTab from './designer/ContentTab'
import { useUndoRedo } from './designer/useUndoRedo'

/** Server-decided comb capacity verdict — see app/services/comb_capacity.py. */
export type CombFit = {
  canonical: string
  printed: string
  status: 'empty' | 'fits' | 'abbreviated' | 'truncated_at_word' | 'capacity_exceeded'
  capacity: number
  used: number
  dropped: string
  dropped_chars: number
  abbreviations: [string, string][]
  reason: string
  review_required: boolean
  fits: boolean
}

type PreviewRecord = {
  id: number
  label: string
  values: Record<string, string>
  // ADDRESS CAPACITY POLICY: the characters each comb ROW prints, and the
  // whole-value verdict, both decided by the server. Keyed by document_fields.id
  // (NOT field_key) because a two-row comb has two rows bound to one key that
  // must print DIFFERENT slices of the same decided string.
  //
  // These are deliberately server-supplied rather than computed here: the
  // abbreviate/truncate ladder must exist in exactly one place, or the designer
  // preview drifts away from the PDF and the print view -- which is precisely
  // what happened before 2026-08-12, when the designer drew combs as one
  // continuous string while the PDF engine drew separate cells.
  combRows: Record<number, string>
  combFits: Record<number, CombFit>
}
type MobilePanel = 'palette' | 'canvas' | 'props'

export default function DocumentCustomize() {
  const { tplId } = useParams<{ tplId: string }>()
  const TPL_ID = Number(tplId)
  const navigate = useNavigate()
  const toast = useToast()

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  const [template, setTemplate] = useState<Template | null>(null)
  const [fields, setFields] = useState<DesignerField[]>([])
  const [deletedIds, setDeletedIds] = useState<number[]>([])
  const [selectedUid, setSelectedUid] = useState<string | null>(null)

  const [palette, setPalette] = useState<Palette>({ groups: [] })
  const [backgrounds, setBackgrounds] = useState<BackgroundOpt[]>([])

  const [zoom, setZoom] = useState(0.75)
  const [zoomAnim, setZoomAnim] = useState(false)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState<number[]>([1])
  const [cursorPt, setCursorPt] = useState<{ x: number; y: number } | null>(null)
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>('canvas')
  // Mobile: collapse the dense page-setup controls (background / size /
  // orientation / dimensions) behind a toggle so the Canvas shows the document
  // immediately instead of a tall wall of wrapped toolbar controls. On desktop
  // (lg+) the cluster is always visible.
  const [showPageSetup, setShowPageSetup] = useState(false)
  // Mobile: the Name/Description/Category/Status metadata form is long and
  // pushes the actual designer canvas far below the fold. Collapse it by
  // default on mobile (expandable), always open on md+.
  const [showMeta, setShowMeta] = useState(false)
  // Top-level tabs — matches the reference: "Content" (free-form notes / body)
  // vs "Template Designer" (the coordinate field canvas).
  const [activeTab, setActiveTab] = useState<'content' | 'designer'>('designer')

  // Description meta fence (persisted inside description).
  const [metaLetterhead, setMetaLetterhead] = useState(false)
  const [metaDepositor, setMetaDepositor] = useState(false)
  const [metaNotes, setMetaNotes] = useState('')
  const [bgFit, setBgFit] = useState<BgFit>('fill')
  const [bgNatural, setBgNatural] = useState<{ w: number; h: number } | null>(null)
  const [bgError, setBgError] = useState(false)

  // Live data preview.
  const [previewQuery, setPreviewQuery] = useState('')
  const [previewResults, setPreviewResults] = useState<{ id: number; label: string; sub: string }[]>([])
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewRecord, setPreviewRecord] = useState<PreviewRecord | null>(null)
  const [previewMode, setPreviewMode] = useState(false)

  const canvasRef = useRef<HTMLDivElement | null>(null)
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const fieldsRef = useRef<DesignerField[]>([])
  const touchPickRef = useRef<PaletteDragPayload | null>(null)
  const didAutoFitRef = useRef(false)
  const bgUploadRef = useRef<HTMLInputElement | null>(null)
  useEffect(() => { fieldsRef.current = fields }, [fields])

  const pageW = template ? Number(template.page_width) || 595 : 595
  const pageH = template ? Number(template.page_height) || 842 : 842
  const pageWRef = useRef(pageW); pageWRef.current = pageW
  const pageHRef = useRef(pageH); pageHRef.current = pageH
  const zoomRef = useRef(zoom); zoomRef.current = zoom
  const pageRef = useRef(page); pageRef.current = page

  const markDirty = useCallback((d = true) => setDirty(d), [])

  // Undo/redo for canvas field operations
  const { pushUndo, undo, redo } = useUndoRedo(
    () => fieldsRef.current,
    (restored) => setFields(restored),
    () => markDirty(),
  )

  // ─────────────── Load ───────────────
  useEffect(() => {
    let cancelled = false
    async function bootstrap() {
      setLoading(true)
      try {
        const { data: tpl } = await api.get<Template & { fields?: Partial<DesignerField>[] }>(`/documents/templates/${TPL_ID}`)
        if (cancelled) return
        const fs: DesignerField[] = (tpl.fields || []).map((f) => ({
          _uid: newUid(),
          id: (f.id as number) ?? null,
          label: f.label ?? '',
          field_key: f.field_key ?? '',
          field_type: f.field_type ?? 'text',
          static_value: f.static_value ?? '',
          // Round geometry on load so legacy fields saved with full float
          // precision (e.g. an aspect-locked 212.42857142) show clean values in
          // the inspector instead of an ugly 8-decimal string.
          x: round1(Number(f.x) || 0),
          y: round1(Number(f.y) || 0),
          width: round1(Number(f.width) || defaultWidthFor(f.field_type ?? 'text')),
          height: round1(Number(f.height) || defaultHeightFor(f.field_type ?? 'text')),
          font_size: round1(Number(f.font_size) || 11),
          font_bold: !!f.font_bold,
          font_italic: !!f.font_italic,
          color: f.color || '#000000',
          align: (f.align as DesignerField['align']) || 'left',
          page: Number(f.page) || 1,
          meta: (f.meta as DesignerField['meta']) || {},
        }))
        const md = parseMetaFromDescription(tpl.description || '')
        setTemplate({
          ...tpl,
          page_size: tpl.page_size || 'a4',
          orientation: (tpl.orientation as Template['orientation']) || 'portrait',
          page_width: Number(tpl.page_width) || 595,
          page_height: Number(tpl.page_height) || 842,
          data_source: tpl.data_source || 'candidate',
          category: tpl.category || 'Protector Documents',
          background_image: tpl.background_image || '',
          is_active: tpl.is_active !== false,
          description: (tpl.description || '').replace(META_FENCE_RE, '').trim(),
        })
        setFields(fs)
        setDeletedIds([])
        setMetaLetterhead(!!md.letterhead)
        setMetaDepositor(!!md.depositor)
        setMetaNotes(md.notes || '')
        setBgFit(BG_FIT_VALUES.includes(md.bg_fit as BgFit) ? (md.bg_fit as BgFit) : 'fill')
        // Pages derived from persisted fields.
        const pgs = new Set<number>([1])
        for (const f of fs) pgs.add(Number(f.page) || 1)
        const sorted = [...pgs].sort((a, b) => a - b)
        setPages(sorted)
        setPage(sorted[0])

        const [{ data: pal }, { data: bgs }] = await Promise.all([
          api.get<Palette>(`/documents/designer/palette`, { params: { data_source: tpl.data_source || 'candidate' } }),
          api.get<BackgroundOpt[]>(`/documents/designer/backgrounds`).catch(() => ({ data: [] as BackgroundOpt[] })),
        ])
        if (cancelled) return
        setPalette(pal && Array.isArray(pal.groups) ? pal : { groups: [] })
        setBackgrounds(Array.isArray(bgs) ? bgs : [])
        markDirty(false)
      } catch (err) {
        toast.show(apiError(err, 'Failed to load template'), 'error')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    if (Number.isFinite(TPL_ID)) bootstrap()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [TPL_ID])

  // Reload palette when data_source flips.
  const dataSource = template?.data_source
  useEffect(() => {
    if (!dataSource) return
    let cancelled = false
    api.get<Palette>(`/documents/designer/palette`, { params: { data_source: dataSource } })
      .then(({ data }) => { if (!cancelled && data && Array.isArray(data.groups)) setPalette(data) })
      .catch(() => { /* keep previous palette */ })
    return () => { cancelled = true }
  }, [dataSource])

  // Warn before unload if dirty.
  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [dirty])

  // ── Zoom fitting ────────────────────────────────────────────────────────
  // Reported 2026-07-31: "page does not fit 100%" -- the single Fit button was
  // fit-to-WIDTH minus 48px of padding, which lands just under 1.0 on a window
  // only slightly wider than the page (97%), and nothing offered a true 1:1.
  // The visible Fit button now means "best readable canvas view": fit to width
  // but never upscale beyond a true 100% document view. A separate Page control is
  // available for users who explicitly need the full sheet visible vertically.
  // See fitZoom/fitPageZoom in designer/engine.ts for the snap rule.
  const getPadding = useCallback(() => {
    return window.matchMedia('(max-width: 1023px)').matches ? CANVAS_PADDING_MOBILE : CANVAS_PADDING
  }, [])
  const fitToWidth = useCallback(() => {
    const vp = viewportRef.current
    if (!vp) return
    setZoom(fitZoom(vp.clientWidth, pageWRef.current, getPadding()))
  }, [getPadding])
  const fitToPage = useCallback(() => {
    const vp = viewportRef.current
    if (!vp) return
    setZoom(fitPageZoom(vp.clientWidth, vp.clientHeight, pageWRef.current, pageHRef.current, getPadding()))
  }, [getPadding])
  useEffect(() => {
    if (!template || didAutoFitRef.current) return
    didAutoFitRef.current = true
    requestAnimationFrame(() => fitToPage())
  }, [template, fitToPage])
  useEffect(() => {
    let raf = 0
    const onResize = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => fitToWidth()) }
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); cancelAnimationFrame(raf) }
  }, [fitToWidth])

  // Reset measured bg intrinsic size when the chosen background changes.
  const bgImageKey = template?.background_image
  useEffect(() => { setBgNatural(null); setBgError(false) }, [bgImageKey])

  // ─────────────── Template metadata updaters ───────────────
  const setTpl = <K extends keyof Template>(k: K, v: Template[K]) => {
    setTemplate((prev) => (prev ? { ...prev, [k]: v } : prev))
    markDirty()
  }
  const applyPageGeometry = (size: string, orient: 'portrait' | 'landscape') => {
    if (!template) return
    const { w, h } = resolvePageGeometry(size, orient, template.page_width, template.page_height)
    setTemplate((prev) => (prev ? { ...prev, page_size: size, orientation: orient, page_width: w, page_height: h } : prev))
    markDirty()
  }
  const setCustomDim = (which: 'w' | 'h', v: number) => {
    if (!template) return
    setTemplate((prev) => prev
      ? { ...prev, page_size: 'custom', page_width: which === 'w' ? (v || 0) : prev.page_width, page_height: which === 'h' ? (v || 0) : prev.page_height }
      : prev)
    markDirty()
  }

  // Background select with ASPECT-LOCK: snap page height
  // to the chosen background's true aspect ratio so the stretched-to-fill scan
  // is never distorted and designer == print == PDF.
  const chooseBackground = (fname: string) => {
    if (!template) return
    let patch: Partial<Template> = { background_image: fname }
    const bg = backgrounds.find((b) => b.filename === fname)
    if (fname && bg && bg.width && bg.height) {
      const newH = Math.round(((pageW * bg.height) / bg.width) * 10) / 10
      if (newH > 0 && Math.abs(newH - pageH) >= 0.5) {
        patch = { ...patch, page_size: 'custom', page_height: newH }
      }
    }
    setTemplate((prev) => (prev ? { ...prev, ...patch } : prev))
    markDirty()
  }

  // Background UPLOAD → POST /templates/:id/background (server aspect-locks
  // page_height and returns the new geometry).
  const uploadBackground = async (file: File) => {
    if (!template) return
    const fd = new FormData()
    fd.append('file', file)
    try {
      const { data } = await api.post<{ background_image: string; page_width: number; page_height: number }>(
        `/documents/templates/${TPL_ID}/background`, fd, { headers: { 'Content-Type': 'multipart/form-data' } })
      setTemplate((prev) => prev
        ? { ...prev, background_image: data.background_image, page_width: data.page_width || prev.page_width, page_height: data.page_height || prev.page_height, page_size: 'custom' }
        : prev)
      // Refresh the backgrounds list so the new file appears in the dropdown.
      api.get<BackgroundOpt[]>(`/documents/designer/backgrounds`)
        .then(({ data: bgs }) => setBackgrounds(Array.isArray(bgs) ? bgs : []))
        .catch(() => {})
      markDirty()
      toast.show('Background uploaded', 'success')
    } catch (err) {
      toast.show(apiError(err, 'Background upload failed'), 'error')
    }
  }

  // ─────────────── Field CRUD ───────────────
  const insertField = useCallback((opts: {
    key?: string; label?: string; ftype?: string; static_value?: string; x_pt?: number; y_pt?: number
  }) => {
    const t = opts.ftype || 'text'
    const PH = pageHRef.current
    const f: DesignerField = {
      _uid: newUid(),
      id: null,
      label: opts.label || opts.key || 'Field',
      field_key: opts.key || '',
      field_type: t,
      static_value: opts.static_value || '',
      x: Math.max(0, round1(opts.x_pt ?? 40)),
      y: Math.max(0, round1(opts.y_pt ?? PH - 60)),
      width: defaultWidthFor(t),
      height: defaultHeightFor(t),
      font_size: t === 'char_cells' ? 12 : 11,
      font_bold: false,
      font_italic: false,
      color: '#000000',
      align: t === 'checkbox' ? 'center' : 'left',
      page: pageRef.current,
      meta: defaultMetaFor(t),
    }
    setFields((prev) => [...prev, f])
    setSelectedUid(f._uid)
    markDirty()
  }, [markDirty])

  const insertPrimitive = (kind: 'text' | 'checkbox' | 'barcode' | 'trade_table') => {
    const PH = pageHRef.current
    if (kind === 'text') return insertField({ key: '__static__', label: 'Text', ftype: 'static', static_value: 'New text', x_pt: 50, y_pt: PH - 100 })
    if (kind === 'checkbox') return insertField({ key: '__static_checkbox__', label: 'Checkbox', ftype: 'checkbox', x_pt: 50, y_pt: PH - 100 })
    if (kind === 'barcode') return insertField({ key: '__static_barcode__', label: 'Barcode', ftype: 'barcode', x_pt: 50, y_pt: PH - 100 })
    if (kind === 'trade_table') return insertField({ key: 'trades_table', label: 'Trades Table', ftype: 'trade_table', x_pt: 50, y_pt: PH - 120 })
  }
  const addCustomText = (v: string) =>
    insertField({ key: '__static__', label: v.slice(0, 30), ftype: 'static', static_value: v, x_pt: 50, y_pt: pageHRef.current - 100 })

  const insertFromPalette = (p: PaletteDragPayload) =>
    insertField({
      key: p.key, label: p.label, ftype: p.ftype,
      x_pt: 40,
      y_pt: pageHRef.current - 60 - ((fieldsRef.current.length * 18) % (pageHRef.current - 80)),
    })

  const patchField = useCallback((uid: string, patch: Partial<DesignerField>) => {
    setFields((prev) => prev.map((f) => (f._uid === uid ? { ...f, ...patch } : f)))
    markDirty()
  }, [markDirty])

  const deleteSelected = useCallback(() => {
    setSelectedUid((uid) => {
      if (!uid) return uid
      setFields((prev) => {
        const f = prev.find((x) => x._uid === uid)
        if (f?.id) setDeletedIds((d) => [...d, f.id as number])
        return prev.filter((x) => x._uid !== uid)
      })
      markDirty()
      return null
    })
  }, [markDirty])

  const duplicateSelected = () => {
    if (!selectedUid) return
    setFields((prev) => {
      const f = prev.find((x) => x._uid === selectedUid)
      if (!f) return prev
      const clone: DesignerField = { ...f, _uid: newUid(), id: null, x: (f.x || 0) + 10, y: Math.max(0, (f.y || 0) - 10) }
      setSelectedUid(clone._uid)
      return [...prev, clone]
    })
    markDirty()
  }

  // ─────────────── Drag / Resize (pointer events) ───────────────
  const beginDrag = useCallback((uid: string, e: React.PointerEvent) => {
    if ((e.target as HTMLElement).dataset.role === 'resize-handle') return
    const f = fieldsRef.current.find((x) => x._uid === uid)
    if (!f) return
    pushUndo()
    setSelectedUid(uid)
    const startX = e.clientX
    const startY = e.clientY
    const x0 = f.x, y0 = f.y
    const el = e.currentTarget as HTMLElement
    try { el.setPointerCapture(e.pointerId) } catch { /* older browsers */ }
    const onMove = (ev: PointerEvent) => {
      const z = zoomRef.current
      const dx = (ev.clientX - startX) / z
      const dy = (ev.clientY - startY) / z
      const { x: nx, y: ny } = clampFieldPosition(
        x0 + dx,
        y0 - dy,
        Number(f.width) || 1,
        Number(f.height) || 1,
        pageWRef.current,
        pageHRef.current,
      )
      patchField(uid, { x: nx, y: ny })
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    e.preventDefault()
  }, [patchField])

  const beginResize = useCallback((uid: string, e: React.PointerEvent) => {
    e.stopPropagation()
    e.preventDefault()
    const f = fieldsRef.current.find((x) => x._uid === uid)
    if (!f) return
    pushUndo()
    const startX = e.clientX
    const startY = e.clientY
    const x0 = f.x, y0 = f.y
    const w0 = f.width, h0 = f.height
    const handle = e.currentTarget as HTMLElement
    try { handle.setPointerCapture(e.pointerId) } catch { /* older browsers */ }
    const onMove = (ev: PointerEvent) => {
      const z = zoomRef.current
      const dx = (ev.clientX - startX) / z
      const dy = (ev.clientY - startY) / z
      // Bottom-right resize keeps the TOP edge anchored. In PDF coordinates that
      // means increasing visual height must also move stored y downward; merely
      // changing height keeps the bottom fixed and makes the handle drift away
      // from the pointer.
      const next = clampFieldBox({
        x: x0,
        y: y0 - dy,
        width: w0 + dx,
        height: h0 + dy,
      }, pageWRef.current, pageHRef.current)
      const nextW = Math.max(8, next.width)
      // A char_cells grid must keep fitting the box it lives in. Without this,
      // shrinking the box leaves the old (now impossible) cell_width behind --
      // the exact way OEP Form "phone" ended up with 409pt of cells in a
      // 99.6pt box. refitCellMeta returns the same object when nothing needs
      // changing, so non-grid fields cost nothing.
      const nextMeta = f.field_type === 'char_cells' ? refitCellMeta(f.meta, nextW) : f.meta
      patchField(uid, {
        y: next.y, width: nextW, height: Math.max(8, next.height),
        ...(nextMeta !== f.meta ? { meta: nextMeta } : {}),
      })
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [patchField])

  // ─────────────── Canvas drop / touch-insert / cursor coords ───────────────
  const onCanvasDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const raw = e.dataTransfer.getData('text/x-palette')
    if (!raw) return
    let p: PaletteDragPayload
    try { p = JSON.parse(raw) } catch { return }
    const rect = canvasRef.current!.getBoundingClientRect()
    const wPt = defaultWidthFor(p.ftype)
    const hPt = defaultHeightFor(p.ftype)
    const point = clientToCanvasPoint(e.clientX, e.clientY, rect, zoomRef.current)
    const box = canvasPointToPdfBox(point, wPt, hPt, pageWRef.current, pageHRef.current)
    insertField({
      key: p.key,
      label: p.label,
      ftype: p.ftype,
      static_value: p.staticValue,
      x_pt: box.x,
      y_pt: box.y,
    })
  }
  const onCanvasTouchEnd = (e: React.TouchEvent) => {
    const p = touchPickRef.current
    if (!p) return
    touchPickRef.current = null
    const touch = e.changedTouches[0]
    const rect = canvasRef.current!.getBoundingClientRect()
    const point = clientToCanvasPoint(touch.clientX, touch.clientY, rect, zoomRef.current)
    if (point.xCss >= 0 && point.yCss >= 0 && point.xCss <= pageW * PT_TO_CSS && point.yCss <= pageH * PT_TO_CSS) {
      const wPt = defaultWidthFor(p.ftype)
      const hPt = defaultHeightFor(p.ftype)
      const box = canvasPointToPdfBox(point, wPt, hPt, pageWRef.current, pageHRef.current)
      insertField({
        key: p.key,
        label: p.label,
        ftype: p.ftype,
        static_value: p.staticValue,
        x_pt: box.x,
        y_pt: box.y,
      })
    }
  }
  const onCanvasMouseMove = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect()
    const { xCss, yCss } = clientToCanvasPoint(e.clientX, e.clientY, rect, zoomRef.current)
    setCursorPt({ x: round1(xCss / PT_TO_CSS), y: round1(pageH - yCss / PT_TO_CSS) })
  }

  // ─────────────── Keyboard shortcuts (nudge / delete) ───────────────
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return
      // Undo/redo works globally (no field selection needed)
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) { undo(); e.preventDefault(); return }
      if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) { redo(); e.preventDefault(); return }
      const uid = selectedUid
      if (!uid) return
      const f = fieldsRef.current.find((x) => x._uid === uid)
      if (!f) return
      const step = e.shiftKey ? 10 : 1
      const PW = pageWRef.current, PH = pageHRef.current
      if (e.key === 'ArrowLeft') { pushUndo(); patchField(uid, clampFieldPosition((f.x || 0) - step, f.y || 0, f.width || 1, f.height || 1, PW, PH)); e.preventDefault() }
      else if (e.key === 'ArrowRight') { pushUndo(); patchField(uid, clampFieldPosition((f.x || 0) + step, f.y || 0, f.width || 1, f.height || 1, PW, PH)); e.preventDefault() }
      else if (e.key === 'ArrowUp') { pushUndo(); patchField(uid, clampFieldPosition(f.x || 0, (f.y || 0) + step, f.width || 1, f.height || 1, PW, PH)); e.preventDefault() }
      else if (e.key === 'ArrowDown') { pushUndo(); patchField(uid, clampFieldPosition(f.x || 0, (f.y || 0) - step, f.width || 1, f.height || 1, PW, PH)); e.preventDefault() }
      else if (e.key === 'Delete' || e.key === 'Backspace') { pushUndo(); deleteSelected(); e.preventDefault() }
    }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [selectedUid, patchField, deleteSelected, undo, redo, pushUndo])

  // ─────────────── Page tabs ───────────────
  const addPage = () => {
    const next = (Math.max(...pages) || 0) + 1
    setPages((p) => [...p, next])
    setPage(next)
  }
  const delPage = (pg: number) => {
    if (pages.length <= 1) return
    // Move fields off the deleted page to page 1.
    setFields((prev) => prev.map((f) => ((Number(f.page) || 1) === pg ? { ...f, page: 1 } : f)))
    const rest = pages.filter((p) => p !== pg)
    setPages(rest)
    setPage(rest[0])
    markDirty()
  }

  // ─────────────── Zoom ───────────────
  const zoomTo = (z: number) => {
    setZoomAnim(true)
    setZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, +z.toFixed(3))))
    setTimeout(() => setZoomAnim(false), 220)
  }

  // ─────────────── Live data preview ───────────────
  useEffect(() => {
    const q = previewQuery.trim()
    if (q.length < 2) { setPreviewResults([]); setPreviewOpen(false); return }
    const t = setTimeout(async () => {
      try {
        const ds = template?.data_source || 'candidate'
        let url = `/candidates/?q=${encodeURIComponent(q)}&limit=10`
        if (ds === 'demand') url = `/demands/?q=${encodeURIComponent(q)}&limit=10`
        else if (ds === 'client') url = `/clients/?q=${encodeURIComponent(q)}&limit=10`
        const { data } = await api.get(url)
        const items: Record<string, unknown>[] = Array.isArray(data) ? data : (data.items || [])
        setPreviewResults(items.map((r) => ({
          id: Number(r.id),
          label: String(r.full_name || r.company_name || r.file_number || `#${r.id}`),
          sub: String(r.passport_no || r.file_number || r.email || ''),
        })))
        setPreviewOpen(true)
      } catch { /* search failed silently */ }
    }, 220)
    return () => clearTimeout(t)
  }, [previewQuery, template?.data_source])

  const selectPreviewRecord = async (rid: number, label: string) => {
    setPreviewOpen(false)
    setPreviewQuery(label)
    try {
      const { data } = await api.get<{
        fields?: {
          id: number; field_key: string; value: string
          comb_row?: string; comb_fit?: CombFit
        }[]
      }>(
        `/documents/templates/${TPL_ID}/preview-data`, { params: { record_id: rid } })
      const values: Record<string, string> = {}
      const combRows: Record<number, string> = {}
      const combFits: Record<number, CombFit> = {}
      for (const f of data.fields || []) {
        values[f.field_key] = f.value
        // Capacity decision comes down with the payload; the browser never
        // recomputes it (see the CombFit comment on PreviewRecord).
        if (f.comb_row !== undefined && f.id != null) combRows[f.id] = f.comb_row
        if (f.comb_fit && f.id != null) combFits[f.id] = f.comb_fit
      }
      setPreviewRecord({ id: rid, label, values, combRows, combFits })
      setPreviewMode(true)
      toast.show(`Loaded ${Object.keys(values).length} values for "${label}"`, 'success')
    } catch {
      toast.show('Preview load failed', 'error')
      setPreviewRecord(null)
    }
  }
  const openPrintPreview = () => {
    if (!previewRecord) { toast.show('Pick a record first', 'error'); return }
    window.open(`/api/documents/templates/${TPL_ID}/print?record_id=${previewRecord.id}&auto=1`, '_blank')
  }
  const openRecord = () => {
    if (!previewRecord) { toast.show('Pick a record first', 'error'); return }
    const ds = template?.data_source || 'candidate'
    const path = ds === 'demand' ? `/demands/${previewRecord.id}` : `/candidates#${previewRecord.id}`
    window.open(path, '_blank')
  }

  // ─────────────── Save ───────────────
  const save = async () => {
    if (!template) return
    setSaving(true)
    try {
      const description = serializeDescription(template.description || '', {
        letterhead: metaLetterhead, depositor: metaDepositor, notes: metaNotes, bg_fit: bgFit,
      })
      await api.put(`/documents/templates/${TPL_ID}`, {
        name: template.name?.trim() || '',
        description,
        category: template.category,
        data_source: template.data_source,
        is_active: !!template.is_active,
        background_image: template.background_image || '',
        page_width: Number(template.page_width) || 595,
        page_height: Number(template.page_height) || 842,
        page_size: template.page_size || 'a4',
        orientation: template.orientation || 'portrait',
      })
      const payload = fields.map((f) => ({
        id: f.id, label: f.label || '', field_key: f.field_key || '', field_type: f.field_type || 'text',
        static_value: f.static_value || '', x: Number(f.x) || 0, y: Number(f.y) || 0,
        width: Number(f.width) || 0, height: Number(f.height) || 0,
        font_size: Number(f.font_size) || 11, font_bold: !!f.font_bold, font_italic: !!f.font_italic,
        color: f.color || '#000000', align: f.align || 'left', page: Number(f.page) || 1, meta: f.meta || {},
      }))
      const { data } = await api.post<{ total_fields?: number; fields?: { id: number; field_key: string; x: number; y: number; page: number }[] }>(
        `/documents/templates/${TPL_ID}/fields/bulk`, { fields: payload, deleted_ids: deletedIds })
      // Reconcile server-assigned ids onto still-transient new rows.
      if (Array.isArray(data?.fields)) {
        const byKey: Record<string, { id: number }> = {}
        for (const srv of data.fields) byKey[`${srv.field_key}|${srv.x}|${srv.y}|${srv.page}`] = srv
        setFields((prev) => prev.map((f) => {
          if (f.id) return f
          const hit = byKey[`${f.field_key || ''}|${f.x}|${f.y}|${f.page || 1}`]
          return hit ? { ...f, id: hit.id } : f
        }))
      }
      setDeletedIds([])
      markDirty(false)
      toast.show(`Saved · ${data?.total_fields ?? fields.length} fields persisted`, 'success')
    } catch (err) {
      toast.show(apiError(err, 'Save failed'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const cancel = () => {
    if (!dirty || window.confirm('You have unsaved changes. Discard them and leave the designer?')) {
      navigate('/documents')
    }
  }

  // ─────────────── Derived ───────────────
  const selected = fields.find((f) => f._uid === selectedUid) || null
  const placedKeys = useMemo(() => new Set(fields.map((f) => f.field_key)), [fields])
  const pageFields = fields.filter((f) => (Number(f.page) || 1) === page)
  const previewValues = previewMode && previewRecord ? previewRecord.values : null

  // ─────────────── Render ───────────────
  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-400">
        <i className="fa-solid fa-spinner fa-spin text-blue-600 mr-2" /> Loading designer…
      </div>
    )
  }
  if (!template) return <div className="p-6 text-rose-600 text-sm">Template not found.</div>

  const bg = (template.background_image || '').trim()

  return (
    <div className="text-[13px]">
      {/* ───── Breadcrumb + primary actions ───── */}
      <div className="mb-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <button onClick={cancel} className="text-xs text-slate-500 hover:text-blue-600 inline-flex items-center gap-1">
            <i className="fa-solid fa-arrow-left" /> Back to Documents
          </button>
          <span className="text-slate-300">/</span>
          <span className="text-xs font-semibold text-slate-700">Customize Document</span>
          {dirty && (
            <span className="ml-2 text-[10px] uppercase tracking-wide font-bold text-amber-600 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full">
              <i className="fa-solid fa-circle text-[6px] align-middle" /> Unsaved
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={cancel} className="px-3 py-1.5 text-xs font-semibold text-slate-700 bg-white hover:bg-slate-100 border border-slate-300 rounded-md">
            <i className="fa-solid fa-xmark mr-1" /> Cancel
          </button>
          <button disabled={saving} onClick={save} className="px-4 py-1.5 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded-md disabled:opacity-50">
            <i className={`fa-solid ${saving ? 'fa-spinner fa-spin' : 'fa-floppy-disk'} mr-1`} /> {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      {/* ───── Top metadata bar ───── */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-4 mb-3">
        {/* Mobile collapse header — keeps the tall form out of the way so the
            designer canvas is reachable without a long scroll. Always open md+. */}
        <button
          type="button"
          onClick={() => setShowMeta((s) => !s)}
          className="md:hidden w-full flex items-center justify-between text-left mb-1"
          aria-expanded={showMeta}
        >
          <span className="text-sm font-bold text-slate-800 truncate pr-2">
            <i className="fa-solid fa-circle-info text-blue-500 mr-1.5" />
            {template.name || 'Document details'}
          </span>
          <span className="text-[11px] font-semibold text-blue-600 shrink-0 flex items-center gap-1">
            {showMeta ? 'Hide' : 'Edit details'}
            <i className={`fa-solid fa-chevron-down text-[9px] transition-transform ${showMeta ? 'rotate-180' : ''}`} />
          </span>
        </button>
        <div className={`${showMeta ? 'block' : 'hidden'} md:block`}>
        <div className="grid grid-cols-12 gap-3">
          <label className="col-span-12 md:col-span-3">
            <span className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide">Name</span>
            <input value={template.name || ''} onChange={(e) => setTpl('name', e.target.value)} className="mt-1 w-full px-2.5 py-1.5 text-sm border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none" />
          </label>
          <label className="col-span-12 md:col-span-3">
            <span className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide">Description</span>
            <input value={template.description || ''} onChange={(e) => setTpl('description', e.target.value)} className="mt-1 w-full px-2.5 py-1.5 text-sm border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none" />
          </label>
          <label className="col-span-6 md:col-span-2">
            <span className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide">Category</span>
            <select value={template.category} onChange={(e) => setTpl('category', e.target.value)} className="mt-1 w-full px-2.5 py-1.5 text-sm border border-slate-300 rounded-md outline-none bg-white">
              <option value="Protector Documents">Protector Documents (system)</option>
              <option value="Visa Process">Visa Process (system)</option>
              <option value="bank">Bank (system)</option>
              <option value="custom">Custom</option>
            </select>
          </label>
          <label className="col-span-6 md:col-span-2">
            <span className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide">Attach to</span>
            <select value={template.data_source} onChange={(e) => setTpl('data_source', e.target.value)} className="mt-1 w-full px-2.5 py-1.5 text-sm border border-slate-300 rounded-md outline-none bg-white">
              <option value="candidate">Candidate</option>
              <option value="demand">Demand File</option>
              <option value="client">Client / Sponsor</option>
              <option value="agent">Sub-Agent</option>
            </select>
          </label>
          <label className="col-span-6 md:col-span-2">
            <span className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide">Status</span>
            <select value={template.is_active ? '1' : '0'} onChange={(e) => setTpl('is_active', e.target.value === '1')} className="mt-1 w-full px-2.5 py-1.5 text-sm border border-slate-300 rounded-md outline-none bg-white">
              <option value="1">Enabled</option>
              <option value="0">Disabled</option>
            </select>
          </label>
        </div>
        {/* Switch-style toggles (match reference: full descriptive labels) */}
        <div className="mt-3 pt-3 border-t border-slate-100 space-y-2.5">
          <label className="inline-flex items-center gap-2.5 cursor-pointer select-none text-slate-700 text-[13px]">
            <span className={`relative inline-block w-9 h-5 rounded-full transition ${metaLetterhead ? 'bg-blue-600' : 'bg-slate-300'}`}>
              <input type="checkbox" className="sr-only" checked={metaLetterhead} onChange={(e) => { setMetaLetterhead(e.target.checked); markDirty() }} />
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${metaLetterhead ? 'translate-x-4' : ''}`} />
            </span>
            Use letterhead when printing (aligned with fields)
          </label>
          <label className="flex items-center gap-2.5 cursor-pointer select-none text-slate-700 text-[13px]">
            <span className={`relative inline-block w-9 h-5 rounded-full transition ${metaDepositor ? 'bg-blue-600' : 'bg-slate-300'}`}>
              <input type="checkbox" className="sr-only" checked={metaDepositor} onChange={(e) => { setMetaDepositor(e.target.checked); markDirty() }} />
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${metaDepositor ? 'translate-x-4' : ''}`} />
            </span>
            Requires depositor (show depositor selector when printing)
          </label>
        </div>
        </div>
      </div>

      {/* ───── Content / Template Designer tabs (match reference) ───── */}
      <div className="flex items-center gap-6 border-b border-slate-200 mb-3 px-1">
        {([['content', 'Content'], ['designer', 'Template Designer']] as const).map(([k, lbl]) => (
          <button
            key={k}
            onClick={() => setActiveTab(k)}
            className={`relative pb-2 pt-1 text-sm font-semibold transition ${activeTab === k ? 'text-blue-700' : 'text-slate-500 hover:text-slate-700'}`}
          >
            {lbl}
            {activeTab === k && <span className="absolute left-0 right-0 -bottom-px h-0.5 bg-blue-600 rounded-full" />}
          </button>
        ))}
      </div>

      {/* ───── CONTENT TAB — WYSIWYG rich-text editor with field palette ───── */}
      {activeTab === 'content' && (
        <ContentTab
          value={metaNotes}
          onChange={(v) => { setMetaNotes(v); markDirty() }}
          palette={palette}
          pageSize={template.page_size || 'a4'}
          onPageSizeChange={(v) => applyPageGeometry(v, template.orientation)}
          onAddPage={addPage}
        />
      )}

      {activeTab === 'designer' && (
      <>
      

      {/* ───── Mobile panel switcher ───── */}
      <div className="lg:hidden flex gap-0.5 mb-2 p-0.5 bg-slate-100 border border-slate-200 rounded-lg select-none sticky top-0 z-30" role="tablist" aria-label="Designer panels">
        {([['palette', 'fa-shapes', 'Fields'], ['canvas', 'fa-file-lines', 'Canvas'], ['props', 'fa-sliders', 'Props']] as const).map(([k, icon, lbl]) => (
          <button
            key={k}
            role="tab"
            aria-selected={mobilePanel === k}
            onClick={() => setMobilePanel(k)}
            className={`flex-1 flex items-center justify-center gap-1 py-1.5 text-[11.5px] font-semibold rounded-md transition ${mobilePanel === k ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500'}`}
          >
            <i className={`fa-solid ${icon} text-[11px]`} /> {lbl}
          </button>
        ))}
      </div>

      {/* ───── Main 3-column workspace ───── */}
      <div className="grid grid-cols-12 gap-2 lg:gap-3">
        {/* ── Palette ── */}
        <aside className={`col-span-12 lg:col-span-3 ${mobilePanel !== 'palette' ? 'hidden lg:block' : ''}`}>
          <PalettePanel
            palette={palette}
            placedKeys={placedKeys}
            onInsertPrimitive={insertPrimitive}
            onAddCustomText={addCustomText}
            onInsertField={insertFromPalette}
            onTouchPick={(p) => { touchPickRef.current = p }}
          />
        </aside>

        {/* ── Canvas ── */}
        <section className={`col-span-12 lg:col-span-6 ${mobilePanel !== 'canvas' ? 'hidden lg:block' : ''}`}>
          <div className="bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col" style={{ minHeight: 'min(540px, 70vh)' }}>
            {/* Canvas toolbar */}
            <div className="px-3 py-2 border-b border-slate-100 flex items-center justify-between gap-2 flex-wrap">
              {/* Page tabs */}
              <div className="inline-flex items-center gap-1 flex-wrap">
                {pages.map((p) => (
                  <button
                    key={p}
                    onClick={() => setPage(p)}
                    className={`px-3 py-1 text-xs font-semibold rounded-md cursor-pointer transition ${p === page ? 'bg-blue-600 text-white shadow' : 'text-slate-600 hover:text-blue-600'}`}
                  >
                    Page {p}
                    {pages.length > 1 && p === page && (
                      <span onClick={(e) => { e.stopPropagation(); delPage(p) }} className="ml-1 text-[10px] opacity-60 hover:opacity-100" title="Delete page">×</span>
                    )}
                  </button>
                ))}
                <button onClick={addPage} className="px-3 py-1 text-xs font-semibold text-slate-400 hover:text-blue-600 rounded-md" title="Add page"><i className="fa-solid fa-plus" /></button>
              </div>
              {/* Mobile: toggle to reveal the dense page-setup controls. */}
              <button
                type="button"
                onClick={() => setShowPageSetup((s) => !s)}
                className="lg:hidden ml-auto px-2.5 py-1 text-xs font-semibold border border-slate-300 rounded-md bg-white text-slate-600 hover:bg-slate-50 inline-flex items-center gap-1.5"
                aria-expanded={showPageSetup}
              >
                <i className="fa-solid fa-sliders text-[11px]" /> Page setup
                <i className={`fa-solid fa-chevron-down text-[9px] transition-transform ${showPageSetup ? 'rotate-180' : ''}`} />
              </button>

              {/* Background · size · orientation · zoom.
                  Hidden on mobile until "Page setup" is toggled; always shown on lg+. */}
              <div className={`${showPageSetup ? 'flex' : 'hidden'} lg:flex items-center gap-2 text-xs flex-wrap w-full lg:w-auto mt-2 lg:mt-0`}>
                <label className="inline-flex items-center gap-1.5">
                  <span className="text-slate-500">Background</span>
                  <select value={bgFilename(template.background_image)} onChange={(e) => chooseBackground(e.target.value)} className="px-2 py-1 text-xs border border-slate-300 rounded-md bg-white max-w-[180px] truncate">
                    <option value="">— None —</option>
                    {backgrounds.map((b) => <option key={b.filename} value={b.filename}>{b.filename}</option>)}
                  </select>
                </label>
                <input ref={bgUploadRef} type="file" accept=".png,.jpg,.jpeg" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadBackground(f); e.target.value = '' }} />
                <button onClick={() => bgUploadRef.current?.click()} title="Upload a new background image (PNG/JPG)" className="px-2 py-1 text-xs border border-slate-300 rounded-md bg-white hover:bg-slate-50 text-slate-600">
                  <i className="fa-solid fa-upload mr-1" />Upload
                </button>
                {bg && (
                  <>
                    <button onClick={() => chooseBackground('')} title="Clear the background" className="px-2 py-1 text-xs border border-slate-300 rounded-md bg-white hover:bg-rose-50 hover:border-rose-300 hover:text-rose-600 text-slate-600 transition">
                      <i className="fa-solid fa-eraser mr-1" />Clear
                    </button>
                    <select
                      value={bgFit}
                      title="How the background image is placed. Use ‘Contain’ or ‘Actual’ for barcodes, logos and stamps so they are never stretched."
                      onChange={(e) => { setBgFit(e.target.value as BgFit); markDirty() }}
                      className="px-2 py-1 text-xs border border-slate-300 rounded-md bg-white"
                    >
                      <option value="fill">Fit: Fill page</option>
                      <option value="contain">Fit: Contain</option>
                      <option value="actual">Fit: Actual 1:1</option>
                    </select>
                  </>
                )}
                <label className="inline-flex items-center gap-1.5">
                  <span className="text-slate-500">Size</span>
                  <select value={template.page_size} onChange={(e) => applyPageGeometry(e.target.value, template.orientation)} className="px-2 py-1 text-xs border border-slate-300 rounded-md bg-white">
                    {Object.entries(PAGE_SIZES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                  </select>
                </label>
                <div className="inline-flex rounded-md border border-slate-300 overflow-hidden">
                  <button onClick={() => applyPageGeometry(template.page_size, 'portrait')} className={`px-2 py-1 text-xs ${template.orientation === 'portrait' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`} title="Portrait"><i className="fa-solid fa-file" /></button>
                  <button onClick={() => applyPageGeometry(template.page_size, 'landscape')} className={`px-2 py-1 text-xs ${template.orientation === 'landscape' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`} title="Landscape"><i className="fa-solid fa-file fa-rotate-90" /></button>
                </div>
                {template.page_size === 'custom' && (
                  <span className="inline-flex items-center gap-1">
                    <input type="number" value={Math.round(pageW)} onChange={(e) => setCustomDim('w', parseFloat(e.target.value))} className="w-14 px-1.5 py-1 text-xs border border-slate-300 rounded" title="Width (pt)" />
                    <span className="text-slate-400">×</span>
                    <input type="number" value={Math.round(pageH)} onChange={(e) => setCustomDim('h', parseFloat(e.target.value))} className="w-14 px-1.5 py-1 text-xs border border-slate-300 rounded" title="Height (pt)" />
                  </span>
                )}
                <span className="text-[10px] text-slate-400">{Math.round(pageW)}×{Math.round(pageH)}pt · {ptToMm(pageW).toFixed(0)}×{ptToMm(pageH).toFixed(0)}mm</span>
              </div>
              {/* Zoom — always visible (mobile + desktop). */}
              <div className="inline-flex items-center gap-0.5 ml-auto border border-slate-300 rounded-md overflow-hidden bg-white h-[26px]">
                <button onClick={() => zoomTo(zoom - 0.1)} className="w-7 h-full text-slate-600 hover:bg-slate-100 flex items-center justify-center transition" title="Zoom out"><i className="fa-solid fa-magnifying-glass-minus text-[11px]" /></button>
                <span className="text-xs font-semibold text-slate-700 min-w-[3.25rem] text-center border-x border-slate-200 h-full flex items-center justify-center select-none tabular-nums">{Math.round(zoom * 100)}%</span>
                <button onClick={() => zoomTo(zoom + 0.1)} className="w-7 h-full text-slate-600 hover:bg-slate-100 flex items-center justify-center transition" title="Zoom in"><i className="fa-solid fa-magnifying-glass-plus text-[11px]" /></button>
                <button
                  id="designer-zoom-fit-width"
                  onClick={() => { setZoomAnim(true); fitToWidth(); setTimeout(() => setZoomAnim(false), 220) }}
                  className="px-2 h-full text-[11px] font-semibold text-slate-600 hover:bg-slate-100 border-l border-slate-200 flex items-center transition"
                  title="Fit to available width; shows 100% when the page already fits"
                >Fit</button>
                <button
                  id="designer-zoom-fit-page"
                  onClick={() => { setZoomAnim(true); fitToPage(); setTimeout(() => setZoomAnim(false), 220) }}
                  className="px-2 h-full text-[11px] font-semibold text-slate-600 hover:bg-slate-100 border-l border-slate-200 flex items-center transition"
                  title="Fit the whole page vertically and horizontally"
                >Page</button>
                <button
                  id="designer-zoom-actual"
                  onClick={() => zoomTo(1)}
                  className="px-2 h-full text-[11px] font-semibold text-slate-600 hover:bg-slate-100 border-l border-slate-200 flex items-center transition"
                  title="Actual size (1 pt = 1 px)"
                >100%</button>
              </div>
            </div>

            {/* Live data preview bar */}
            <div className="px-3 py-2 border-b border-slate-100 bg-gradient-to-r from-blue-50 via-white to-slate-50 flex items-center gap-2 flex-wrap">
              <span className="text-[10px] font-bold text-blue-700 uppercase tracking-wider"><i className="fa-solid fa-bolt" /> Live data preview</span>
              <div className="relative flex-1 min-w-[200px] max-w-[360px]">
                <input
                  value={previewQuery}
                  onChange={(e) => setPreviewQuery(e.target.value)}
                  onBlur={() => setTimeout(() => setPreviewOpen(false), 180)}
                  type="text"
                  placeholder="Search candidate by name / passport / file #…"
                  className="w-full pl-7 pr-2 py-1 text-xs border border-blue-300 rounded-md focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white"
                />
                <i className="fa-solid fa-magnifying-glass absolute left-2.5 top-1.5 text-blue-500 text-xs" />
                {previewOpen && (
                  <div className="absolute z-30 left-0 right-0 top-full mt-1 bg-white border border-blue-200 rounded-md shadow-lg max-h-60 overflow-y-auto">
                    {previewResults.length === 0
                      ? <div className="px-3 py-2 text-xs text-slate-400">No matches</div>
                      : previewResults.map((r) => (
                        <button key={r.id} type="button" onMouseDown={() => selectPreviewRecord(r.id, r.label)} className="w-full text-left px-3 py-1.5 hover:bg-blue-50 border-b border-slate-100 last:border-0">
                          <div className="text-xs font-semibold text-slate-800">{r.label}</div>
                          {r.sub && <div className="text-[10px] text-slate-500 font-mono">{r.sub}</div>}
                        </button>
                      ))}
                  </div>
                )}
              </div>
              {previewRecord && <span className="text-[11px] text-slate-500">· <b className="text-emerald-700">{previewRecord.label}</b></span>}
              <label className="inline-flex items-center gap-1.5 cursor-pointer ml-auto text-[11px] text-slate-700">
                <input type="checkbox" checked={previewMode} onChange={(e) => setPreviewMode(e.target.checked)} />
                Preview data on canvas
              </label>
              <button onClick={openPrintPreview} disabled={!previewRecord} className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded-md disabled:opacity-50" title="Open the print preview with the selected record's real data">
                <i className="fa-solid fa-print" /> Print preview
              </button>
              <button onClick={openRecord} disabled={!previewRecord} className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold text-slate-700 bg-white hover:bg-slate-50 border border-slate-300 rounded-md disabled:opacity-50" title="Open this record">
                <i className="fa-solid fa-arrow-up-right-from-square" /> Open record
              </button>
            </div>

            {/* Canvas viewport — p-2 on mobile to maximise usable area, p-6 on desktop */}
            <div ref={viewportRef} className="flex-1 overflow-auto bg-slate-200 p-2 lg:p-6" style={{ minHeight: 360, WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain' }}>
              <div className="relative mx-auto" style={{ width: pageW * zoom, height: pageH * zoom }}>
                <div
                  ref={canvasRef}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={onCanvasDrop}
                  onTouchEnd={onCanvasTouchEnd}
                  onMouseMove={onCanvasMouseMove}
                  onMouseLeave={() => setCursorPt(null)}
                  onMouseDown={(e) => { if (e.target === canvasRef.current) setSelectedUid(null) }}
                  className="relative bg-white shadow-xl select-none"
                  style={{
                    width: pageW, height: pageH,
                    transform: `scale(${zoom})`,
                    transformOrigin: 'top left',
                    transition: zoomAnim ? 'transform .16s cubic-bezier(.22,.61,.36,1)' : undefined,
                  }}
                >
                  {/* Background image */}
                  {bg && !bgError && (() => {
                    const style: React.CSSProperties = {
                      position: 'absolute', userSelect: 'none', pointerEvents: 'none',
                      // Smooth high-quality resampling for scanned document
                      // backgrounds — 'crisp-edges' made photographic scans look
                      // pixelated/harsh at fractional zoom. 'auto' (bilinear)
                      // gives the clean, high-fidelity look of the reference.
                      imageRendering: 'auto',
                      backfaceVisibility: 'hidden',
                    }
                    if (bgFit === 'fill') Object.assign(style, { inset: 0, width: '100%', height: '100%', objectFit: 'fill' as const })
                    else if (bgFit === 'contain') Object.assign(style, { inset: 0, width: '100%', height: '100%', objectFit: 'contain' as const, objectPosition: 'top left' })
                    else { style.left = 0; style.top = 0; if (bgNatural) { style.width = bgNatural.w; style.height = bgNatural.h } }
                    return (
                      <img
                        src={bgSrc(bg)}
                        alt=""
                        draggable={false}
                        onLoad={(e) => { const im = e.currentTarget; setBgNatural({ w: im.naturalWidth, h: im.naturalHeight }) }}
                        onError={() => setBgError(true)}
                        style={style}
                      />
                    )
                  })()}
                  {/* Empty / broken-bg state */}
                  {(!bg || bgError) && (
                    <div className="absolute inset-0 flex items-center justify-center text-slate-300 text-sm pointer-events-none">
                      <div className="text-center">
                        {bgError ? (
                          <>
                            <i className="fa-regular fa-circle-xmark text-4xl text-rose-300 block mb-2" />
                            <div className="text-rose-500 text-xs">Background not found:</div>
                            <div className="text-slate-500 text-xs font-mono mt-1">{bg}</div>
                          </>
                        ) : (
                          <>
                            <i className="fa-regular fa-image text-4xl block mb-2" />
                            Select a background to begin
                          </>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Field markers — current page only */}
                  {pageFields.map((f) => (
                    <FieldMarker
                      key={f._uid}
                      field={f}
                      pageH={pageH}
                      selected={selectedUid === f._uid}
                      previewValue={
                        previewValues && !['photo', 'barcode', 'trade_table', 'checkbox'].includes(f.field_type)
                          ? (previewValues[f.field_key] ?? null)
                          : null
                      }
                      // Server-decided comb slice + verdict for THIS row. Only
                      // present in preview mode with a loaded record; when
                      // absent FieldMarker falls back to showing the raw token,
                      // which is the un-previewed design-time behaviour.
                      combRow={(previewMode && f.id != null && previewRecord)
                        ? (previewRecord.combRows[f.id] ?? null) : null}
                      combFit={(previewMode && f.id != null && previewRecord)
                        ? (previewRecord.combFits[f.id] ?? null) : null}
                      onPointerDown={beginDrag}
                      onResizeStart={beginResize}
                      onDoubleClick={(uid) => { setSelectedUid(uid); if (window.matchMedia('(max-width: 1023px)').matches) setMobilePanel('props') }}
                    />
                  ))}
                </div>
              </div>
            </div>

            {/* Canvas footer */}
            <div className="px-3 py-1.5 border-t border-slate-100 text-[11px] text-slate-500 flex items-center justify-between">
              <span>{pageFields.length} field{pageFields.length === 1 ? '' : 's'} on page {page} · {fields.length} total</span>
              <span className="font-mono">{cursorPt ? `x: ${cursorPt.x.toFixed(1)} · y: ${cursorPt.y.toFixed(1)}` : 'x: — · y: —'}</span>
            </div>
          </div>
        </section>

        {/* ── Properties ── */}
        <aside className={`col-span-12 lg:col-span-3 ${mobilePanel !== 'props' ? 'hidden lg:block' : ''}`}>
          <div className="bg-white border border-slate-200 rounded-xl shadow-sm" style={{ maxHeight: 'calc(100vh - 220px)', overflowY: 'auto', minHeight: 'min(540px, 60vh)' }}>
            <div className="px-3 py-2 border-b border-slate-100">
              <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Properties</h3>
            </div>
            <PropertiesPanel
              field={selected}
              palette={palette}
              pageW={pageW}
              pageH={pageH}
              onPatch={(patch) => selected && patchField(selected._uid, patch)}
              onPatchMeta={(key, value) => selected && patchField(selected._uid, { meta: { ...(selected.meta || {}), [key]: value } })}
              onDelete={deleteSelected}
              onDuplicate={duplicateSelected}
            />
          </div>
        </aside>
      </div>

      {/* Mobile: floating "Edit properties" affordance when a field is selected. */}
      {selected && mobilePanel === 'canvas' && (
        <button
          type="button"
          onClick={() => setMobilePanel('props')}
          className="lg:hidden fixed left-1/2 -translate-x-1/2 z-[70] inline-flex items-center gap-2 px-4 py-2.5 text-[13px] font-semibold text-white rounded-full shadow-lg"
          style={{ bottom: 'calc(env(safe-area-inset-bottom, 0px) + 16px)', background: 'var(--brand, #0071e3)' }}
        >
          <i className="fa-solid fa-sliders" /> Edit properties
        </button>
      )}
      </>
      )}

      {/* FIX 2026-07-21 (CSP) — `.dsg-insert-btn` styles moved to
          src/index.css so the strict `style-src-elem 'self' 'nonce-…'` CSP
          no longer blocks the inline <style> block that lived here. */}
    </div>
  )
}
