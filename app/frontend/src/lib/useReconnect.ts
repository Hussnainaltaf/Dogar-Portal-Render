import { useEffect, useRef, useState } from 'react'

/**
 * useReconnect — fire a callback when the app regains connectivity so callers
 * can refetch and drop any stale, cached-while-offline data.
 *
 * It reacts to:
 *   • the browser `online` event (fired when the network comes back), and
 *   • `visibilitychange` returning to visible while `navigator.onLine` is true
 *     (covers the common "laptop woke from sleep / tab refocused" case where no
 *     explicit offline→online transition fired).
 *
 * The callback is only invoked when the previous known state was offline (for
 * the `online` event) or when the tab was hidden (for visibility), so a healthy
 * connection never triggers spurious refetches.
 *
 * Returns `online` (current connectivity) for optional UI (e.g. an offline
 * banner).
 */
export function useReconnect(onReconnect: () => void): { online: boolean } {
  const [online, setOnline] = useState<boolean>(
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  )
  // Keep the latest callback in a ref so the effect doesn't re-bind on every
  // render (callers usually pass an inline closure).
  const cbRef = useRef(onReconnect)
  useEffect(() => { cbRef.current = onReconnect }, [onReconnect])

  useEffect(() => {
    const goOnline = () => {
      setOnline(true)
      // Coming back from offline → refetch.
      cbRef.current()
    }
    const goOffline = () => setOnline(false)
    const onVisible = () => {
      if (document.visibilityState === 'visible' && navigator.onLine) {
        // Tab refocused with a live connection → refresh to avoid stale rows.
        cbRef.current()
      }
    }

    window.addEventListener('online', goOnline)
    window.addEventListener('offline', goOffline)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener('online', goOnline)
      window.removeEventListener('offline', goOffline)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  return { online }
}

export default useReconnect
