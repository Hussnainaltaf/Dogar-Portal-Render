/**
 * Reference-counted body scroll lock.
 *
 * PROBLEM this solves: multiple independent components (AppLayout mobile
 * drawer, candidate side-sheet, assign wizard, tenant-branding drawer) each
 * used to do `document.body.style.overflow = 'hidden'` on open and restore it
 * on close. When two of them overlapped, one would save the *already-locked*
 * value as its "previous" state and restore `hidden` on close — leaving the
 * whole portal permanently unable to scroll ("portal is not scrolling").
 *
 * This module keeps a single counter. The body is locked while the count is
 * > 0 and fully released (back to its ORIGINAL value) only when the last
 * locker unlocks. Idempotent and StrictMode-safe.
 */
let count = 0
let originalOverflow: string | null = null

export function lockBodyScroll(): void {
  if (count === 0) {
    originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  count += 1
}

export function unlockBodyScroll(): void {
  count = Math.max(0, count - 1)
  if (count === 0) {
    document.body.style.overflow = originalOverflow ?? ''
    originalOverflow = null
  }
}

/** Convenience for React effects: `useEffect(() => useBodyScrollLock(), [])`. */
export function useBodyScrollLock(): () => void {
  lockBodyScroll()
  return unlockBodyScroll
}
