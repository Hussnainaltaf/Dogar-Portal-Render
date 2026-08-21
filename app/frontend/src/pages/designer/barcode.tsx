/**
 * Live barcode / QR preview for designer markers.
 * Reuses the SAME local vendor libraries as the legacy designer
 * (/static/vendor/js/JsBarcode.all.min.js and qrcode.min.js), lazy-loaded on
 * first use, so the on-canvas preview is identical in both implementations.
 * The exported PDF still uses ReportLab server-side — this is preview-only.
 */
import { useEffect, useRef, useState } from 'react'
import type { FieldMeta } from './types'

declare global {
  interface Window {
    JsBarcode?: (el: SVGSVGElement, value: string, opts: Record<string, unknown>) => void
    qrcode?: (typeNumber: number, ecc: string) => {
      addData: (v: string) => void
      make: () => void
      createDataURL: (cellSize: number, margin: number) => string
    }
  }
}

const loaded: Record<string, Promise<void>> = {}
function loadScript(src: string): Promise<void> {
  if (!loaded[src]) {
    loaded[src] = new Promise((resolve, reject) => {
      const s = document.createElement('script')
      s.src = src
      s.onload = () => resolve()
      s.onerror = () => reject(new Error(`load failed: ${src}`))
      document.head.appendChild(s)
    })
  }
  return loaded[src]
}
const ensureJsBarcode = () => (window.JsBarcode ? Promise.resolve() : loadScript('/static/vendor/js/JsBarcode.all.min.js'))
const ensureQrcode = () => (window.qrcode ? Promise.resolve() : loadScript('/static/vendor/js/qrcode.min.js'))

/** Normalize the symbology value to a canonical form. */
export function normSymbology(meta: FieldMeta): string {
  let fmt = String((meta.symbology as string) || (meta.format as string) || 'code128')
    .toLowerCase()
    .replace(/[-_]/g, '')
  if (fmt === 'qrcode') fmt = 'qr'
  return fmt
}

export function BarcodePreview({ value, meta }: { value: string; meta: FieldMeta }) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const [qrUri, setQrUri] = useState<string>('')
  const [fallback, setFallback] = useState<string>('')
  const fmt = normSymbology(meta)
  const showText = meta.show_text !== false

  useEffect(() => {
    let cancelled = false
    setFallback('')
    if (fmt === 'qr') {
      setQrUri('')
      ensureQrcode()
        .then(() => {
          if (cancelled || !window.qrcode) throw new Error('qrcode lib unavailable')
          const qr = window.qrcode(0, String(meta.qr_ecc || 'M'))
          qr.addData(value)
          qr.make()
          if (!cancelled) setQrUri(qr.createDataURL(6, 0))
        })
        .catch(() => { if (!cancelled) setFallback('▦ QR: ' + value) })
      return () => { cancelled = true }
    }
    // Linear barcodes via JsBarcode.
    setQrUri('')
    const fmtMap: Record<string, string> = { code128: 'CODE128', code39: 'CODE39', ean13: 'EAN13', ean: 'EAN13', upc: 'UPC' }
    const jsFmt = fmtMap[fmt] || 'CODE128'
    ensureJsBarcode()
      .then(() => {
        if (cancelled || !window.JsBarcode || !svgRef.current) throw new Error('JsBarcode unavailable')
        window.JsBarcode(svgRef.current, value, {
          format: jsFmt,
          displayValue: showText,
          margin: 2,
          fontSize: 12,
          textMargin: 2,
          height: 40,
          width: 1.2,
        })
        // Preserve aspect ratio so preview matches the undistorted PDF output.
        svgRef.current.setAttribute('preserveAspectRatio', 'xMidYMid meet')
        svgRef.current.style.width = '100%'
        svgRef.current.style.height = '100%'
      })
      .catch(() => { if (!cancelled) setFallback('||| ' + value + ' |||') })
    return () => { cancelled = true }
  }, [value, fmt, showText, meta.qr_ecc])

  if (fallback) {
    return <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: fmt === 'qr' ? 9 : 10, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis' }}>{fallback}</span>
  }
  if (fmt === 'qr') {
    return qrUri
      ? <img src={qrUri} alt="" style={{ display: 'block', width: '100%', height: '100%', imageRendering: 'pixelated', objectFit: 'contain' }} draggable={false} />
      : null
  }
  return (
    <svg
      ref={svgRef}
      style={{ position: 'absolute', left: 1, right: 1, top: 10, width: 'calc(100% - 2px)', height: 'calc(100% - 12px)', pointerEvents: 'none', background: '#fff' }}
    />
  )
}
