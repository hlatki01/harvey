import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authFetch, authToken, pendingInvite, sameOriginUrl, setAuthToken, takeFragmentToken } from './auth'

const TOKEN = 'A'.repeat(43)

beforeEach(() => {
  sessionStorage.clear()
  history.replaceState(null, '', '/')
})

describe('takeFragmentToken', () => {
  it('moves #token into sessionStorage and cleans the address bar', () => {
    history.replaceState(null, '', '/#token=' + TOKEN)
    takeFragmentToken()
    expect(authToken()).toBe(TOKEN)
    expect(window.location.hash).toBe('')
  })
  it('keeps an invite only in sessionStorage', () => {
    history.replaceState(null, '', '/#invite=' + TOKEN)
    takeFragmentToken()
    expect(pendingInvite()).toBe(TOKEN)
    expect(window.location.hash).toBe('')
  })
  it('drops a malformed token but still cleans the URL', () => {
    history.replaceState(null, '', '/#token=short')
    takeFragmentToken()
    expect(authToken()).toBe('')
    expect(window.location.hash).toBe('')
  })
})

describe('setAuthToken', () => {
  it('rejects values that are not tokens', () => {
    expect(setAuthToken('nope')).toBe(false)
    expect(setAuthToken(TOKEN)).toBe(true)
  })
})

describe('authFetch', () => {
  it('refuses off-origin and protocol-relative targets', async () => {
    expect(sameOriginUrl('//evil.example/x')).toBeNull()
    expect(sameOriginUrl('https://evil.example/x')).toBeNull()
    expect(sameOriginUrl('/a\\b')).toBeNull()
    await expect(authFetch('//evil.example/x')).rejects.toThrow()
  })
  it('sends the bearer token same-origin with no redirects or referrer', async () => {
    setAuthToken(TOKEN)
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'))
    await authFetch('/api/today')
    const init = spy.mock.calls[0][1] as RequestInit
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer ' + TOKEN)
    expect(init.redirect).toBe('error')
    expect(init.referrerPolicy).toBe('no-referrer')
    expect(init.credentials).toBe('same-origin')
    spy.mockRestore()
  })
})

describe('auth matrix (ported from test_dashboard_auth)', () => {
  it('strips a bad #token=<script> fragment without storing it', () => {
    history.replaceState(null, '', '/#token=<script>')
    takeFragmentToken()
    expect(authToken()).toBe('')
    expect(window.location.hash).toBe('')
    expect(sessionStorage.length).toBe(0)
  })
  it('sameOriginUrl accepts only absolute same-origin paths', () => {
    const o = window.location.origin
    const got = ['/api/x', '//evil.example/api', 'https://evil.example/api', '/\\evil.example',
      'javascript:alert(1)', 'api/x', '/api/x?all=true'].map(p => sameOriginUrl(p)?.href ?? null)
    expect(got).toEqual([o + '/api/x', null, null, null, null, null, o + '/api/x?all=true'])
  })
  it('authFetch call shape; off-origin never reaches fetch', async () => {
    setAuthToken(TOKEN)
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'))
    await expect(authFetch('https://evil.example/api')).rejects.toThrow()
    expect(spy).not.toHaveBeenCalled()
    await authFetch('/api/companies', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    expect(spy).toHaveBeenCalledTimes(1)
    const [url, init] = spy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(window.location.origin + '/api/companies')
    expect((init.headers as Headers).get('content-type')).toBe('application/json')
    expect(init.mode).toBe('same-origin')
    expect(init.method).toBe('POST')
    spy.mockRestore()
  })
})
