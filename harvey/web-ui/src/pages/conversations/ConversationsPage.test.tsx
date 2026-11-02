import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NavProvider } from '@/app/nav'
import type { Convo } from './types'

const DATA: Convo[] = [
  { id: 'other', status: 'needs_human' },
  { id: 'bello', status: 'needs_human' },
  { id: 'done', status: 'closed' },
]
vi.mock('@/lib/hooks', () => ({ useApi: () => ({ data: DATA, refetch: vi.fn() }), useInvalidate: () => vi.fn() }))
vi.mock('./ConversationCard', () => ({ ConversationCard: ({ c }: { c: Convo }) => <p>card {c.id}</p> }))

import { ConversationsPage } from './ConversationsPage'

function open(id: string) {
  history.replaceState(null, '', '/?conversation=' + id)
  return render(<NavProvider><ConversationsPage /></NavProvider>)
}
const pressed = () => screen.getAllByRole('button', { pressed: true }).map(b => b.textContent)

describe('conversation link from an alert', () => {
  it('lands on Open with that conversation shown', () => {
    open('bello')
    expect(pressed()[0]).toMatch(/^Open/)
    expect(screen.getByText('card bello')).toBeInTheDocument()
    expect(window.location.search).toBe('')
  })

  it('falls back to All for a conversation Open does not hold', () => {
    open('done')
    expect(pressed()[0]).toMatch(/^All/)
    expect(screen.getByText('card done')).toBeInTheDocument()
  })
})
