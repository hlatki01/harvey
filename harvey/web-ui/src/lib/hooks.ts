import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useSyncExternalStore } from 'react'
import { api, lock } from './api'

/** True while the tab is hidden: live views stop polling instead of burning requests. */
function usePageHidden() {
  return useSyncExternalStore(
    fn => { document.addEventListener('visibilitychange', fn); return () => document.removeEventListener('visibilitychange', fn) },
    () => document.hidden,
  )
}

/** GET a dashboard API path. `data` is null when offline/locked/non-2xx, and
 *  undefined until the first answer. `interval` (ms) polls while the tab is
 *  visible and the dashboard isn't locked. */
export function useApi<T = any>(path: string | null, opts: { interval?: number; enabled?: boolean } = {}) {
  const hidden = usePageHidden()
  const locked = useLockState().locked
  const q = useQuery<T | null>({
    queryKey: ['api', path],
    queryFn: () => api<T>(path as string),
    enabled: path !== null && opts.enabled !== false && !locked,
    refetchInterval: opts.interval && !hidden && !locked ? opts.interval : false,
    staleTime: 0,
  })
  return { data: q.data, loading: q.isPending && q.fetchStatus !== 'idle', refetch: q.refetch }
}

/** Refetch every query whose key path starts with one of the prefixes. */
export function useInvalidate() {
  const qc = useQueryClient()
  return (...prefixes: string[]) =>
    qc.invalidateQueries({ predicate: q => {
      const p = q.queryKey[1]
      return typeof p === 'string' && prefixes.some(x => p.startsWith(x))
    } })
}

export function useLockState() {
  return useSyncExternalStore(lock.subscribe, lock.get)
}

/** Run `fn` on an interval while the page is visible. */
export function useInterval(fn: () => void, ms: number) {
  useEffect(() => {
    const id = setInterval(() => { if (!document.hidden) fn() }, ms)
    return () => clearInterval(id)
  }, [fn, ms])
}
