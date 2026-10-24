// Dashboard authorisation. Ported 1:1 from the old web/auth.js; the rules:
//
// The token arrives once, in the link's #fragment (browsers never put
// fragments in HTTP requests or Referer), is moved to sessionStorage (this tab
// only) and removed from the address bar before anything else runs. It is only
// ever sent as an Authorization header, and only to this origin: never a
// cookie, never a query string, never another host.
//
// Invited people use the public address: they sign in with email and password
// and the browser holds an HttpOnly session cookie. An invite link carries its
// one-time token in the #fragment too, kept only until it is accepted.

const AUTH_KEY = 'harvey-dashboard-token'
const INVITE_KEY = 'harvey-invite'
const AUTH_TOKEN_RE = /^[A-Za-z0-9_-]{43,128}$/

export function authToken(): string {
  try { return sessionStorage.getItem(AUTH_KEY) || '' } catch { return '' }
}

export function setAuthToken(token: string): boolean {
  if (!AUTH_TOKEN_RE.test(token || '')) return false
  try { sessionStorage.setItem(AUTH_KEY, token); return true } catch { return false }
}

export function clearAuthToken(): void {
  try { sessionStorage.removeItem(AUTH_KEY) } catch { /* ignore */ }
}

export function pendingInvite(): string {
  try { return sessionStorage.getItem(INVITE_KEY) || '' } catch { return '' }
}

export function clearInvite(): void {
  try { sessionStorage.removeItem(INVITE_KEY) } catch { /* ignore */ }
}

/** True on the public address (invited users), false on localhost. */
export function onPublicHost(): boolean {
  return !['localhost', '127.0.0.1', '[::1]', '::1'].includes(window.location.hostname)
}

/** Move #token / #invite out of the address bar. Must run before anything else. */
export function takeFragmentToken(): void {
  const hash = window.location.hash || ''
  if (!hash) return
  const params = new URLSearchParams(hash.slice(1))
  const token = params.get('token')
  const invite = params.get('invite')
  history.replaceState(null, '', window.location.pathname + window.location.search)
  if (token && AUTH_TOKEN_RE.test(token)) setAuthToken(token)
  if (invite && AUTH_TOKEN_RE.test(invite)) {
    try { sessionStorage.setItem(INVITE_KEY, invite) } catch { /* ignore */ }
  }
}

/** A same-origin URL for an absolute path ("/api/..."), or null. */
export function sameOriginUrl(path: unknown): URL | null {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') ||
      path.includes('\\')) return null
  let url: URL
  try { url = new URL(path, window.location.origin) } catch { return null }
  return url.origin === window.location.origin ? url : null
}

/** fetch() for this dashboard's own API, with the token attached. Refuses
 *  anything that isn't same-origin, and never follows redirects. */
export function authFetch(path: string, opts?: RequestInit): Promise<Response> {
  const url = sameOriginUrl(path)
  if (!url) return Promise.reject(new Error('refusing to send the dashboard token off-origin'))
  const o: RequestInit = { ...(opts || {}) }
  const headers = new Headers(o.headers || {})
  const token = authToken()
  if (token) headers.set('Authorization', 'Bearer ' + token)
  o.headers = headers
  o.mode = 'same-origin'
  o.credentials = 'same-origin'
  o.cache = 'no-store'
  o.redirect = 'error'
  o.referrerPolicy = 'no-referrer'
  return fetch(url.href, o)
}
