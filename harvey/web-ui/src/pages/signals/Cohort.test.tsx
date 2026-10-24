import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CohortBuilder, type Signal } from './Cohort'
import { lock } from '@/lib/api'
import { setAuthToken } from '@/lib/auth'

const TOKEN = 'T'.repeat(43)
const signals: Signal[] = [
  { code: 'A', label: 'Sig A', status: 'confirmed' }, { code: 'B', label: 'Sig B', status: 'confirmed' },
]
const page = (name: string, offset: number) => ({ size: 100, offset, companies: [{ name }] })
const json = (o: unknown) => new Response(JSON.stringify(o), { status: 200 })

beforeEach(() => { sessionStorage.clear(); setAuthToken(TOKEN); lock.set({ locked: false, reason: '' }) })
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

describe('cohort export', () => {
  it('uses authFetch, clicks a blob link, revokes it, and reports failures', async () => {
    const calls: { url: string; auth: string | null; redirect?: string }[] = []
    let next: Response
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      const u = String(url)
      if (u.includes('/api/cohort.csv')) {
        calls.push({ url: u, auth: (init!.headers as Headers).get('Authorization'), redirect: init!.redirect })
        return next
      }
      return json(page('Alpha', 0))
    })
    const revoked: string[] = []
    URL.createObjectURL = () => 'blob:local'
    URL.revokeObjectURL = u => { revoked.push(u) }
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(<CohortBuilder signals={signals} />)
    fireEvent.click(screen.getAllByRole('checkbox')[0]) // first group = "Must have"
    await screen.findByText('Alpha')
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    const btn = () => screen.getByRole('button', { name: /Export/ })

    next = new Response('x', { status: 200 })
    await act(async () => { fireEvent.click(btn()) })
    expect(click).toHaveBeenCalledTimes(1)
    act(() => { vi.advanceTimersByTime(10000) })
    expect(revoked).toEqual(['blob:local'])
    expect(btn()).toHaveTextContent('Export CSV')
    expect(btn()).not.toBeDisabled()

    next = new Response('', { status: 500 })
    await act(async () => { fireEvent.click(btn()) })
    expect(btn()).toHaveTextContent('Export failed — retry')
    expect(lock.get().locked).toBe(false)

    next = new Response('', { status: 401 })
    await act(async () => { fireEvent.click(btn()) })
    expect(btn()).toHaveTextContent('Export failed — retry')
    expect(lock.get().locked).toBe(true)

    expect(calls).toHaveLength(3)
    expect(calls.every(c => c.auth === 'Bearer ' + TOKEN && c.redirect === 'error')).toBe(true)
    expect(calls[0].url).toBe(window.location.origin + '/api/cohort.csv?require=A&exclude=')
  })
})

describe('cohort paging', () => {
  it('ignores superseded pages and cleared filters', async () => {
    const pending: { resolve: (r: Response) => void; body: any }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation((_u, init) =>
      new Promise<Response>(resolve => pending.push({ resolve, body: JSON.parse(String(init!.body)) })))
    const resolve = async (i: number, p: unknown) => { await act(async () => { pending[i].resolve(json(p)) }) }

    render(<CohortBuilder signals={signals} />)
    const boxA = screen.getAllByRole('checkbox')[0]
    fireEvent.click(boxA)                                   // request 0: offset 0
    await waitFor(() => expect(pending).toHaveLength(1))
    expect(screen.getByText('Loading cohort…')).toBeInTheDocument()
    expect(screen.queryByText('Export CSV')).toBeNull()

    await resolve(0, page('P1', 0))
    await screen.findByText('P1')
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))   // request 1: offset 50
    await waitFor(() => expect(pending).toHaveLength(2))
    // supersede it by toggling a filter (request 2) before 1 returns
    fireEvent.click(screen.getAllByRole('checkbox')[1])
    await waitFor(() => expect(pending).toHaveLength(3))
    await resolve(2, page('LATEST', 0))
    await screen.findByText('LATEST')
    await resolve(1, page('STALE', 50))
    expect(screen.queryByText('STALE')).toBeNull()
    expect(screen.getByText('LATEST')).toBeInTheDocument()

    // clearing every required signal empties the view; a late response must not resurrect it
    fireEvent.click(screen.getAllByRole('checkbox')[1]) // un-pick second -> request 3
    await waitFor(() => expect(pending).toHaveLength(4))
    fireEvent.click(boxA)                                // clear require -> idle
    await resolve(3, page('STALE_AFTER_CLEAR', 0))
    expect(screen.queryByText('STALE_AFTER_CLEAR')).toBeNull()
    expect(screen.queryByText('Export CSV')).toBeNull()
    expect(screen.queryByText('Loading cohort…')).toBeNull()
    expect(pending.map(p => p.body.offset)).toEqual([0, 50, 0, 0])
  })
})
