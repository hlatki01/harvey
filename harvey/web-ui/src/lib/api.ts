import { authFetch } from './auth'

// ── Locked state ──
// No token, a wrong one, or a server that can't load its own: every API call
// that comes back 401/503 flips this, and the shell shows the lock screen.

export interface LockState { locked: boolean; reason: string }
let lockState: LockState = { locked: false, reason: '' }
const lockListeners = new Set<() => void>()

export const lock = {
  get: () => lockState,
  subscribe(fn: () => void) { lockListeners.add(fn); return () => { lockListeners.delete(fn) } },
  set(next: LockState) { lockState = next; lockListeners.forEach(fn => fn()) },
  clear() { if (lockState.locked) lock.set({ locked: false, reason: '' }) },
}

/** True (and the lock screen is raised) when the server refused this tab. */
export async function lockIfRefused(r: Response): Promise<boolean> {
  if (r.status !== 401 && r.status !== 503) {
    if (r.ok) lock.clear()
    return false
  }
  let why = ''
  if (r.status === 503) {
    try { why = ((await r.json()) || {}).message || '' } catch { /* ignore */ }
  }
  lock.set({
    locked: true,
    reason: r.status === 503
      ? "The dashboard server can't use its token file, so it is refusing every request. " + why
      : 'refused',
  })
  return true
}

export const JSON_HEADERS = { 'Content-Type': 'application/json' }

/** Read-or-act call. Reads resolve to null on any failure (offline, locked,
 *  non-2xx). An action the server refused resolves to {success:false, ...body}
 *  so the caller can show why ("a run is already going"). */
export async function api<T = any>(path: string, opts?: RequestInit): Promise<T | null> {
  try {
    const r = await authFetch(path, opts)
    if (await lockIfRefused(r)) return null
    if (!r.ok) {
      if (!opts || !opts.method || opts.method === 'GET') return null
      const body = await r.json().catch(() => null)
      return body && typeof body === 'object' ? ({ success: false, ...body } as T) : null
    }
    return (await r.json()) as T
  } catch {
    return null
  }
}

export function post<T = any>(path: string, body?: unknown): Promise<T | null> {
  return api<T>(path, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

/** Downloads go through the same authorised fetch and are handed to the
 *  browser as a local blob, so no download URL ever carries the token. */
export async function downloadFile(path: string, filename: string): Promise<boolean> {
  const r = await authFetch(path)
  if (await lockIfRefused(r)) return false
  if (!r.ok) return false
  const href = URL.createObjectURL(await r.blob())
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 10000)
  return true
}
