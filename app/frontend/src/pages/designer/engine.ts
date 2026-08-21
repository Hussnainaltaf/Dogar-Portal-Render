/**
 * Document Designer — Coordinate Engine
 * ─────────────────────────────────────────────────────────────────────────
 * Pure, dependency-free functions that define the SINGLE source of truth for
 * every coordinate conversion in the designer. The same math is used by the
 * print view (documents.py) and the PDF engine (pdf_engine.py), so the
 * designer canvas, the in-browser print overlay and the exported PDF are all
 * pixel-identical.
 *
 * Coordinate systems:
 *   - STORED       : PDF points, origin = bottom-left (y grows UP).
 *   - CANVAS (CSS) : CSS pixels, origin = top-left (y grows DOWN).
 *   - 1 PDF point == 1 CSS px at zoom 1.0 (PT_TO_CSS = 1); zoom is applied
 *     via a CSS transform on the canvas, never baked into coordinates.
 *
 * Mapping:
 *   cssLeft = x
 *   cssTop  = pageH - y - height
 * Inverted on drag/resize to convert mouse deltas back into PDF coords.
 */
// Type-only import: types.ts imports nothing, so this cannot form a cycle and
// is erased at build time, keeping this module dependency-free at runtime.
import type { FieldMeta } from './types'

/** 1 PDF point = 1 CSS px at zoom 1.0 (kept as a named constant for clarity). */
export const PT_TO_CSS = 1

/**
 * Arial/Helvetica cap+ascent ratio — MUST match ASCENT in the print view
 * (documents.py) and the PDF engine baseline math so designer == print == PDF.
 */
export const ASCENT_RATIO = 0.8

export const ZOOM_MIN = 0.25
export const ZOOM_MAX = 2.0

export interface CanvasPoint {
  xCss: number
  yCss: number
}

export interface FieldBox {
  x: number
  y: number
  width: number
  height: number
}

/** Named page-size presets in PDF points (portrait). Landscape swaps w/h.
 *  Matches reportlab.lib.pagesizes so designer == print == exported PDF. */
export const PAGE_SIZES: Record<string, { label: string; w: number; h: number }> = {
  a4:     { label: 'A4 (210 × 297 mm)',        w: 595, h: 842 },
  letter: { label: 'Letter (8.5 × 11 in)',     w: 612, h: 792 },
  legal:  { label: 'Legal (8.5 × 14 in)',      w: 612, h: 1008 },
  a3:     { label: 'A3 (297 × 420 mm)',        w: 842, h: 1191 },
  a5:     { label: 'A5 (148 × 210 mm)',        w: 420, h: 595 },
  custom: { label: 'Custom',                   w: 595, h: 842 },
}

/** Round to 0.1 pt — the persisted precision used across designer/print/PDF. */
export const round1 = (v: number): number => Math.round(((Number(v) || 0)) * 10) / 10

/** PDF x (pt) → CSS left (px, pre-zoom). */
export const ptToCssLeft = (xPt: number): number => xPt * PT_TO_CSS

/** PDF y (pt, bottom origin) + box height → CSS top (px, pre-zoom). */
export const ptToCssTop = (yPt: number, hPt: number, pageH: number): number =>
  (pageH - yPt - hPt) * PT_TO_CSS

/** CSS left (px, pre-zoom) → PDF x (pt). */
export const cssLeftToPt = (leftCss: number): number => leftCss / PT_TO_CSS

/** CSS top (px, pre-zoom) + box height → PDF y (pt, bottom origin). */
export const cssTopToPt = (topCss: number, hPt: number, pageH: number): number =>
  pageH - (topCss / PT_TO_CSS) - hPt

/** Convert a browser pointer coordinate to an unzoomed canvas coordinate. */
export function clientToCanvasPoint(
  clientX: number,
  clientY: number,
  canvasRect: { left: number; top: number },
  zoom: number,
): CanvasPoint {
  const z = zoom > 0 ? zoom : 1
  return {
    xCss: (clientX - canvasRect.left) / z,
    yCss: (clientY - canvasRect.top) / z,
  }
}

/**
 * Effective box height for text-baseline math. MUST equal the fallback used
 * by the print view (documents.py: `height or max(fs*1.25, 14)`) and the PDF
 * engine so all three renderers place glyphs identically. A stored height is
 * used VERBATIM (never inflated) — inflating small boxes was the source of a
 * designer-vs-print baseline drift for large-font/small-box fields.
 */
export const effectiveHeight = (hPt: number, fontSize: number): number =>
  hPt > 0 ? hPt : Math.max(fontSize * 1.25, 14)

/**
 * Baseline-locked body offset for text-like fields.
 * The glyph baseline is placed at the SAME PDF baseline the print view + PDF
 * engine use:  baseline_pdf_y = y + max(h - font_size, 0).
 * Returns the offset (px, pre-zoom) of the single-line body span measured
 * from the marker box top.
 */
export function baselineBodyTop(yPt: number, hPt: number, fontSize: number, pageH: number): number {
  const baselinePdfY = yPt + Math.max(hPt - fontSize, 0)
  const textTopPt = pageH - (baselinePdfY + ASCENT_RATIO * fontSize)
  const boxTopPt = pageH - yPt - hPt
  return (textTopPt - boxTopPt) * PT_TO_CSS
}

/** Clamp a committed numeric property the same way the reference designer does. */
export function clampProp(
  prop: 'x' | 'y' | 'width' | 'height' | 'font_size' | 'page',
  v: number,
  pageW: number,
  pageH: number,
): number {
  if (Number.isNaN(v)) v = 0
  switch (prop) {
    case 'x':         return Math.min(Math.max(0, v), Math.max(0, pageW - 1))
    case 'y':         return Math.min(Math.max(0, v), Math.max(0, pageH - 1))
    case 'width':     return Math.min(Math.max(1, v), pageW)
    case 'height':    return Math.min(Math.max(1, v), pageH)
    case 'font_size': return Math.min(Math.max(4, v), 200)
    case 'page':      return Math.max(1, Math.round(v))
  }
}

/**
 * Keep a char_cells grid physically possible inside its own box.
 *
 * ROOT CAUSE this guards (audit 2026-08-11): `meta.cell_width` is stored
 * independently of the field box, so resizing (or duplicating onto a smaller
 * box) left the old pitch behind. OEP Form "phone" ended up declaring
 * 13 x 31.47pt = 409pt of cells inside a 99.6pt box -- the pitch inherited
 * from "address" -- which sprays digits across neighbouring fields on the
 * pre-printed government form.
 *
 * Refitting on resize keeps the designer preview, the HTML print path and the
 * PDF engine in agreement, and stops the impossible number ever being typed
 * into the payload. The API enforces the same rule server-side, so this is a
 * UX nicety rather than the only line of defence.
 *
 * Returns a patched copy of `meta`, or the original object when nothing needs
 * changing (so callers can skip a re-render).
 */
export function refitCellMeta(meta: FieldMeta | undefined, width: number): FieldMeta | undefined {
  if (!meta) return meta
  const count = Number(meta.cell_count) || 0
  const gap = Number(meta.cell_gap) || 0
  const stored = Number(meta.cell_width) || 0
  const w = Number(width) || 0
  if (count <= 0 || stored <= 0 || w <= 0) return meta
  // GROUP-BLINDNESS (third copy of the same bug -- see _grid_slot_span in
  // app/api/endpoints/documents.py). A grouped comb spends one blank slot per
  // separator, so a 13-character CNIC printed 5-7-1 physically occupies 15
  // slots. Measuring in characters under-reports the true span by two whole
  // cells, which made this refit BOTH miss real overflow and, worse, "repair"
  // a correctly grouped grid by dividing the width by 13 instead of 15 --
  // silently walking every digit after the first dash out of its printed box
  // the moment an operator nudged the field's width.
  const slots = cellSlotSpan(count, meta.group_sizes)
  if (slots <= 0) return meta
  const needed = stored * slots + gap * Math.max(0, slots - 1)
  if (needed <= w + 1) return meta
  const fitted = (w - gap * Math.max(0, slots - 1)) / slots
  return { ...meta, cell_width: Math.round(Math.max(0, fitted) * 100) / 100 }
}

/**
 * Slot index of each character in a (possibly grouped) char_cells grid.
 *
 * Mirrors `pdf_engine._cell_slots` exactly: each group is followed by one
 * BLANK slot standing in for the separator printed on the form. A malformed
 * `group_sizes` (not summing to `count`) degrades to an even run, matching the
 * defensive contract of `coerce_group_sizes` server-side -- all three engines
 * must agree about what a template means.
 */
export function cellSlots(count: number, groupSizes?: unknown): number[] {
  const n = Math.max(0, Math.floor(Number(count) || 0))
  const sizes = Array.isArray(groupSizes)
    ? (groupSizes as unknown[]).map((v) => Math.floor(Number(v)))
    : null
  if (!sizes || !sizes.length || sizes.some((s) => !Number.isFinite(s) || s <= 0) ||
      sizes.reduce((a, b) => a + b, 0) !== n) {
    return Array.from({ length: n }, (_, i) => i)
  }
  const out: number[] = []
  let slot = 0
  sizes.forEach((size, gi) => {
    for (let k = 0; k < size; k++) out.push(slot++)
    if (gi < sizes.length - 1) slot++ // blank slot for the printed separator
  })
  return out
}

/** How many slot positions wide a char_cells grid really is. */
export function cellSlotSpan(count: number, groupSizes?: unknown): number {
  const slots = cellSlots(count, groupSizes)
  return slots.length ? slots[slots.length - 1] + 1 : 0
}

/** Clamp a whole field box to the page, respecting its own dimensions. */
export function clampFieldBox(box: FieldBox, pageW: number, pageH: number): FieldBox {
  const width = Math.min(Math.max(1, Number(box.width) || 1), Math.max(1, pageW))
  const height = Math.min(Math.max(1, Number(box.height) || 1), Math.max(1, pageH))
  return {
    x: round1(Math.min(Math.max(0, Number(box.x) || 0), Math.max(0, pageW - width))),
    y: round1(Math.min(Math.max(0, Number(box.y) || 0), Math.max(0, pageH - height))),
    width: round1(width),
    height: round1(height),
  }
}

/** Clamp only field position, preserving width/height. */
export function clampFieldPosition(
  x: number,
  y: number,
  width: number,
  height: number,
  pageW: number,
  pageH: number,
): { x: number; y: number } {
  const clamped = clampFieldBox({ x, y, width, height }, pageW, pageH)
  return { x: clamped.x, y: clamped.y }
}

/** Convert an unzoomed canvas point to the stored PDF point for a box. */
export function canvasPointToPdfBox(
  point: CanvasPoint,
  boxWidth: number,
  boxHeight: number,
  pageW: number,
  pageH: number,
): FieldBox {
  return clampFieldBox({
    x: cssLeftToPt(point.xCss),
    y: cssTopToPt(point.yCss, boxHeight, pageH),
    width: boxWidth,
    height: boxHeight,
  }, pageW, pageH)
}

/** Resolve page geometry from a named preset + orientation (presets are portrait). */
export function resolvePageGeometry(
  size: string,
  orient: 'portrait' | 'landscape',
  currentW: number,
  currentH: number,
): { w: number; h: number } {
  const preset = PAGE_SIZES[size] || PAGE_SIZES.a4
  let w: number, h: number
  if (size === 'custom') {
    w = currentW || preset.w
    h = currentH || preset.h
  } else {
    w = preset.w
    h = preset.h
  }
  const lo = Math.min(w, h)
  const hi = Math.max(w, h)
  return orient === 'landscape' ? { w: hi, h: lo } : { w: lo, h: hi }
}

/** pt → mm (for the human-readable page-dimension label). */
export const ptToMm = (pt: number): number => (pt * 25.4) / 72

/**
 * Padding (px, total for both sides) that the canvas viewport reserves around
 * the page. On mobile (<1024px) the viewport uses `p-2` (8px per side = 16px
 * total); on desktop `p-6` (24px per side = 48px total). Exported so the fit
 * math and the markup cannot drift apart silently.
 */
export const CANVAS_PADDING = 48
export const CANVAS_PADDING_MOBILE = 16

/**
 * Zoom that fits the page WIDTH into the viewport.
 *
 * ── Why this used to read 97% and never 100% ──────────────────────────────
 * Reported 2026-07-31: "page does not fit 100% in the coordinate engine",
 * with the indicator stuck at 97% after pressing Fit.
 *
 * That number was arithmetically correct and behaviourally wrong. A 595 pt A4
 * page in a ~1042 px column at the old default 0.75 zoom is fine, but once the
 * browser window is only a little wider than the page, `usable / pageW` lands
 * just under 1 — e.g. (643 - 48) / 595 = 0.9748 → "97%" — so the page rendered
 * slightly shrunk inside grey gutters and there was no control that returned to
 * a true 1:1. Three things were missing rather than one being wrong:
 *
 *   1. a way to fit the WHOLE page (both axes), which is what "Fit" means to
 *      anyone laying out a document;
 *   2. an explicit 100% (1:1) control;
 *   3. a snap, so a fit within half a percent of 1:1 reports 100% instead of a
 *      distracting 99%/97% that implies the canvas is mis-scaled.
 *
 * The snap is safe: at 0.995–1.0 the visual difference is sub-pixel on a 595 pt
 * page, and coordinates are never baked into the zoom (it is a CSS transform),
 * so snapping cannot move a field.
 */
export function fitZoom(
  viewportWidth: number,
  pageW: number,
  padding: number = CANVAS_PADDING,
): number {
  const usable = viewportWidth - padding
  if (usable <= 0) return 1
  // Fill the available viewport width. Cap at ZOOM_MAX (200%) to prevent
  // absurd upscaling, but allow upscale beyond 100% so the background image
  // fills the preview area instead of floating inside grey gutters.
  return clampFit(usable / pageW)
}

/**
 * Zoom that fits the ENTIRE page (both width and height) into the viewport.
 * This is what the "Fit" control uses, so pressing it always shows the whole
 * page rather than just its full width with the bottom scrolled off.
 */
export function fitPageZoom(
  viewportWidth: number,
  viewportHeight: number,
  pageW: number,
  pageH: number,
  padding: number = CANVAS_PADDING,
): number {
  const usableW = viewportWidth - padding
  const usableH = viewportHeight - padding
  if (usableW <= 0 || usableH <= 0) return 1
  return clampFit(Math.min(usableW / pageW, usableH / pageH))
}

/**
 * Clamp a raw fit ratio into the usable zoom band and snap near-unity to
 * exactly 1 so the indicator reads a clean 100%.
 */
function clampFit(raw: number): number {
  if (raw >= 0.995 && raw <= 1.005) return 1
  return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(raw * 1000) / 1000))
}
