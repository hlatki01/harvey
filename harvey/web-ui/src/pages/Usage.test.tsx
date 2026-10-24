import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BrainChips, Quota, UsagePage } from './Usage'

afterEach(() => vi.restoreAllMocks())

const PAUSE = { status: 'paused', provider: 'codex', reason: 'Your Codex login has expired.' }

describe('brain chips', () => {
  it('shows a paused brain and why', () => {
    const { container } = render(<BrainChips d={{ brain_provider: 'codex', brain_status: PAUSE }} />)
    expect(container.innerHTML).toContain('Codex <b>paused</b>')
    expect(container.textContent).toContain('login has expired')
  })
  it('shows nothing paused when healthy, or when the pause is another provider\'s (server sends {})', () => {
    const a = render(<BrainChips d={{ brain_provider: 'codex', brain_status: {} }} />)
    expect(a.container.textContent).not.toContain('paused')
    a.unmount()
    const b = render(<BrainChips d={{ brain_provider: 'claude', brain_status: {} }} />)
    expect(b.container.textContent).not.toContain('paused')
  })
})

describe('quota card', () => {
  it('renders unavailable Codex quota', () => {
    const { container } = render(<Quota d={{ quota_provider: 'codex', quota: undefined }} />)
    expect(screen.getByText('Codex (ChatGPT plan) Quota')).toBeInTheDocument()
    expect(container.textContent).toContain('Quota unavailable')
    expect(container.textContent).toContain('ChatGPT plan')
    expect(screen.queryByRole('meter')).toBeNull()
  })
  it('renders gauges and limit reached; nothing without a provider; Claude unavailable', () => {
    const a = render(<Quota d={{ quota_provider: 'codex', quota: { limit_reached: { utilization: 100, resets_at: '' } } }} />)
    expect(a.container.textContent).toContain('Usage limit reached')
    expect(a.container.textContent).toContain('100%')
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '100')
    a.unmount()
    expect(render(<Quota d={{ quota_provider: '', quota: undefined }} />).container.innerHTML).toBe('')
    const c = render(<Quota d={{ quota_provider: 'claude', quota: undefined }} />)
    expect(c.container.textContent).toContain('Claude Subscription Quota')
    expect(c.container.textContent).toContain('unavailable')
  })
})

describe('Usage page wiring', () => {
  it('takes the pause and call counts from /api/stats (usage has no brain_status)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async url => {
      const u = String(url)
      const body = u.endsWith('/api/stats')
        ? { brain_provider: 'codex', brain_status: PAUSE, codex_calls_today: 3 }
        : { quota_provider: 'codex', quota: null, totals: {}, by_agent: [] }
      return new Response(JSON.stringify(body), { status: 200 })
    })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = render(<QueryClientProvider client={qc}><UsagePage /></QueryClientProvider>)
    await waitFor(() => expect(container.innerHTML).toContain('Codex <b>paused</b>'))
    expect(container.textContent).toContain('Codex calls today 3')
  })
})
