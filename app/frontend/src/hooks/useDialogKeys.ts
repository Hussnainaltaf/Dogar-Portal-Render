import { useEffect, useRef } from 'react'
import { lockBodyScroll, unlockBodyScroll } from '../lib/scrollLock'

/**
 * Keyboard and focus behaviour that EVERY modal overlay in this app must have.
 *
 * WHY THIS HOOK EXISTS
 * --------------------
 * On 2026-08-13 the operator reported dialogs rendering clipped, with their
 * Save buttons unreachable. Fixing the geometry (portalling every overlay to
 * <body>) made them visible -- but an audit on 2026-08-14 found that the
 * *keyboard* half of "usable" had never been implemented consistently:
 *
 *   components/ui.tsx <Modal>      Escape yes, scroll-lock yes, focus no
 *   pages/Candidates.tsx           Escape yes, scroll-lock no,  focus partial
 *   components/AssignWizard.tsx    Escape yes, scroll-lock no,  focus no
 *   pages/MiscPages.tsx  (x3)      Escape NO,  scroll-lock NO,  focus NO
 *   pages/ClientDetail.tsx         Escape NO,  scroll-lock NO,  focus NO
 *
 * A dialog you cannot dismiss from the keyboard is exactly as broken for a
 * keyboard-only operator as a clipped dialog is for a mouse user; it just fails
 * silently instead of visibly. And a dialog whose Tab key walks out into the
 * page behind it is announced as modal (`aria-modal="true"`) while behaving as
 * though it is not -- the aria attribute becomes a lie.
 *
 * Six separate hand-rolled copies of this logic is how the four gaps above
 * appeared in the first place, so this is one hook that all of them call.
 *
 * WHAT IT GUARANTEES
 * ------------------
 *  1. Escape closes the dialog (unless `closeOnEscape` is false -- used while a
 *     save is in flight, where dismissing would abandon a half-written record).
 *  2. `document.body` cannot scroll behind the dialog, via the REFERENCE-COUNTED
 *     lock in lib/scrollLock.ts. Doing this with a local
 *     `document.body.style.overflow` save/restore is a trap the project has
 *     already fallen into once: two overlapping dialogs each saved the
 *     already-locked value as their "previous" state, so the last one to close
 *     restored `hidden` and left the whole portal unable to scroll. See the
 *     header comment of lib/scrollLock.ts.
 *  3. Focus moves into the dialog on open, so the very next Tab lands on a
 *     control inside it rather than on whatever was behind it.
 *  4. Tab and Shift+Tab CYCLE within the dialog instead of escaping into the
 *     inert page. This is what makes `aria-modal="true"` truthful.
 *  5. Focus returns to the element that opened the dialog on close, so the
 *     keyboard user is not dumped back at the top of the document.
 *
 * Usage:
 *
 *     const ref = useDialogKeys<HTMLDivElement>(open, onClose)
 *     ...
 *     <div ref={ref} role="dialog" aria-modal="true"> ... </div>
 *
 * The returned ref goes on the element carrying `role="dialog"` (the CARD, not
 * the backdrop): the backdrop is `role="presentation"` and clicking it closes,
 * so trapping focus on it would be meaningless.
 */
export function useDialogKeys<T extends HTMLElement>(
  open: boolean,
  onClose: () => void,
  opts: { closeOnEscape?: boolean; autoFocus?: boolean } = {},
) {
  const ref = useRef<T | null>(null)
  const { closeOnEscape = true, autoFocus = true } = opts

  // `onClose` is nearly always an inline arrow, so a new function identity
  // arrives on every render. Reading it through a ref keeps the effect from
  // tearing down and re-adding its listener (and re-running the focus move,
  // which would yank focus back to the first field mid-typing) on every render.
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!open) return

    const opener = document.activeElement as HTMLElement | null

    // --- background scroll lock -------------------------------------------
    // Reference-counted on purpose -- see the note in the doc comment above.
    lockBodyScroll()

    // Only elements that are actually focusable AND actually rendered. A
    // display:none control still matches the selector but cannot take focus, so
    // including it would make Tab appear to do nothing at the boundary.
    const FOCUSABLE = [
      'a[href]', 'button:not([disabled])', 'input:not([disabled])',
      'select:not([disabled])', 'textarea:not([disabled])',
      '[tabindex]:not([tabindex="-1"])',
    ].join(',')

    const focusables = (): HTMLElement[] => {
      const root = ref.current
      if (!root) return []
      return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)]
        .filter((el) => el.offsetParent !== null || el === document.activeElement)
    }

    // --- move focus in ----------------------------------------------------
    if (autoFocus) {
      const items = focusables()
      // Prefer a real form field over the close button: landing on "X" means
      // the first Enter press dismisses the dialog the user just opened.
      const firstField = items.find((el) =>
        /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName))
      const target = firstField || items[0] || ref.current
      target?.focus()
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (closeOnEscape) { e.stopPropagation(); closeRef.current() }
        return
      }
      if (e.key !== 'Tab') return

      // --- focus trap ----------------------------------------------------
      const items = focusables()
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement as HTMLElement | null

      // Focus escaped the dialog entirely (browser chrome, or a control removed
      // while focused). Pull it back rather than letting Tab continue into the
      // page behind.
      if (!ref.current?.contains(active)) {
        e.preventDefault()
        ;(e.shiftKey ? last : first).focus()
        return
      }
      if (!e.shiftKey && active === last) { e.preventDefault(); first.focus() }
      else if (e.shiftKey && active === first) { e.preventDefault(); last.focus() }
    }

    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      unlockBodyScroll()
      // Return focus to whatever opened us, but only if it is still in the
      // document -- a row-action button whose row was just deleted is not.
      if (opener && document.contains(opener)) opener.focus()
    }
  }, [open, closeOnEscape, autoFocus])

  return ref
}
