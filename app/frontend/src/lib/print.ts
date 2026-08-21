// Print ANY server-rendered HTML document (file receipt, payment receipt,
// permission letter, packet) via a hidden iframe — no new tab, no popup.
// Faithfully ports printInDemandFrame() from demand_detail.html.
//
// The renderer URL is loaded with `auto=0` / `auto_print=0` so we control the
// print() call from the parent for reliable timing across browsers.

export function printInFrame(url: string, onDone?: () => void) {
  let iframe = document.getElementById('appPrintFrame') as HTMLIFrameElement | null
  if (!iframe) {
    iframe = document.createElement('iframe')
    iframe.id = 'appPrintFrame'
    iframe.setAttribute('aria-hidden', 'true')
    iframe.setAttribute('tabindex', '-1')
    // IMPORTANT: the iframe must have REAL (A4-ish) dimensions and must NOT be
    // display:none / 0x0 / visibility:hidden. Several print drivers rasterize
    // the print job from the iframe's laid-out viewport; a zero-size or
    // visibility:hidden frame yields an EMPTY raster -> the spooler reports the
    // job as "successful" but the printer produces nothing (the exact reported
    // bug). We therefore render it at true A4 size but pushed far off-screen so
    // it is invisible to the user yet fully laid out for the print engine.
    // HEIGHT (audit 2026-08-11): this used to be a rigid `height: 297mm`, i.e.
    // exactly one A4 sheet. Measured in the real print path, the inner document
    // laid out at 320.15mm against a 297.0mm frame -- a 23.15mm overflow, which
    // the print engine faithfully spooled as a SECOND, near-blank sheet. Every
    // document printed from the portal carried that phantom page (a 1-page
    // barcode sheet reported "2 sheets of paper" in the Chrome print dialog).
    //
    // The overflow came from the renderers' SCREEN rule `.sheet{margin:18px
    // auto}`: inside an off-screen iframe the document is still in *screen*
    // media until the print dialog opens, so the 18px top+bottom margin is in
    // the layout when the frame's raster box is fixed. Their `@media print`
    // override lands too late to shrink an already-rigid frame.
    //
    // Fixed on both sides: the renderers now zero that margin for the print
    // frame (see _PRINT_FRAME_RESET in the Python renderers), and the frame
    // itself is no longer pinned to one sheet -- `min-height` lets a genuinely
    // multi-page document (a 4-page form) lay out honestly, while a 1-page
    // document no longer manufactures a second sheet. Width stays exactly
    // 210mm: that one MUST be rigid or the mm-precise field coordinates in the
    // overlay engine would rescale and print out of position.
    Object.assign(iframe.style, {
      position: 'fixed', left: '-10000px', top: '0',
      width: '210mm', minHeight: '297mm', height: 'auto', border: '0',
      opacity: '0', pointerEvents: 'none', zIndex: '-1',
    })
    document.body.appendChild(iframe)
  }

  const u = new URL(url, window.location.origin)
  if (!u.searchParams.has('auto')) u.searchParams.set('auto', '0')
  if (!u.searchParams.has('auto_print')) u.searchParams.set('auto_print', '0')
  u.searchParams.set('_t', String(Date.now()))

  // Tag the framed document so the renderers' `.print-frame` resets apply.
  // Those resets drop the decorative `.sheet{margin:18px auto}` (and, for the
  // coordinate engine, the fit-to-width scale transform) which would otherwise
  // push content ~23mm past A4 and spool a phantom blank sheet.
  //
  // Called from BOTH the onload handler and immediately before print(): the
  // onload path can be skipped entirely if contentDocument access throws, and
  // printing without the reset silently brings the extra page back. Applying
  // it twice is harmless (classList.add is idempotent); never applying it is
  // not.
  const markPrintFrame = () => {
    try {
      const d = iframe!.contentDocument
      if (d?.documentElement) d.documentElement.classList.add('print-frame')
    } catch { /* cross-origin or torn-down frame -- nothing to mark */ }
  }

  const doPrint = () => {
    try {
      const w = iframe!.contentWindow!
      markPrintFrame()
      w.focus()
      w.print()
    } catch (e) {
      // Last-resort fallback: open with auto=1 so the page self-prints
      const fb = new URL(url, window.location.origin)
      fb.searchParams.set('auto', '1')
      fb.searchParams.set('auto_print', '1')
      window.open(fb.toString(), '_blank')
    }
    if (onDone) onDone()
  }

  iframe.onload = null
  iframe.onload = function () {
    // Print as soon as the document's images (barcodes / scanned backgrounds)
    // have finished decoding, instead of a blind fixed delay. This makes the
    // print dialog appear noticeably faster on simple docs while staying
    // reliable on image-heavy ones. A short safety cap guarantees we never
    // hang if an image never resolves.
    let done = false
    const fire = () => { if (!done) { done = true; doPrint() } }
    try {
      // Mark it as early as possible -- before images settle -- so the layout
      // is already correct while we wait, rather than reflowing at print time.
      markPrintFrame()
      const doc = iframe!.contentDocument
      const imgs = doc ? Array.from(doc.images) : []
      const pending = imgs.filter((im) => !im.complete)
      if (pending.length === 0) {
        // Give the layout one frame to settle, then print immediately.
        setTimeout(fire, 60)
      } else {
        let left = pending.length
        const tick = () => { left -= 1; if (left <= 0) setTimeout(fire, 40) }
        pending.forEach((im) => { im.addEventListener('load', tick); im.addEventListener('error', tick) })
      }
    } catch {
      setTimeout(fire, 300)
    }
    // Safety cap — never wait longer than 800ms regardless. Backgrounds are
    // inlined as data: URIs server-side, so images decode near-instantly and a
    // long cap only adds perceived latency.
    setTimeout(fire, 800)
  }
  iframe.src = u.toString()
}
