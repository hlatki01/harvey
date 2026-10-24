import { describe, expect, it, vi } from 'vitest'

describe('boot', () => {
  it('takes a token pasted into an already-open tab (hash-only navigation)', async () => {
    sessionStorage.clear()
    history.replaceState(null, '', '/')
    const reload = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, reload, get hash() { return h }, origin: window.location.origin, pathname: '/', search: '' },
    })
    let h = ''
    await import('./boot')
    const token = 'B'.repeat(43)
    h = '#token=' + token
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    expect(sessionStorage.getItem('harvey-dashboard-token')).toBe(token)
    expect(reload).toHaveBeenCalledOnce()
  })
})
